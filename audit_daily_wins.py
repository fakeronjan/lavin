# =========================================================
# LAVIN — audit daily wins vs Fandom personal-page Challenge History.
#
# For each player with a cached Fandom page, parse the per-season
# "===Challenge History===" tables. Rows where Result column contains
# "WIN" are daily wins. Compare to dailies.csv.
#
# We capture per-season-per-player daily WIN counts. Fandom is treated
# as source of truth for the per-player count. Per-game-name matching
# would also be useful but daily challenge names vary; first focus on
# counts then sample-level game-name agreement.
# =========================================================
import re
from pathlib import Path
import pandas as pd
import mwparserfromhell as mwp

from audit_elim_records import (
    build_season_map, _split_table_rows, _strip_attrs, _plain,
)

HERE = Path(__file__).parent
DATA = HERE / "data"
CACHE = DATA / ".fandom_player_cache"


def parse_player_dailies(wt, player_name, season_map):
    """Yield (season_id, episode, game, result_W_or_L_or_S) from Challenge History."""
    if not wt:
        return
    parts = re.split(r"\n==([^=][^=]*?)==\n", wt)
    for i in range(1, len(parts), 2):
        season_title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        m = re.search(
            r"===\s*Challenge History\s*===\s*\n(\{\|.*?\n\|\})", body, re.DOTALL
        )
        if not m:
            continue
        table_text = m.group(1)

        st = season_title.lower().strip()
        sid = season_map.get(st)
        if not sid:
            st2 = re.sub(r"\(.*?\)", "", st).strip()
            sid = season_map.get(st2)
        if not sid:
            continue

        header, data_rows = _split_table_rows(table_text)
        if not header or not data_rows:
            continue
        # Find Episode, Challenge/Game, Result column indices
        ep_idx = None
        game_idx = None
        res_idx = None
        for j, h in enumerate(header):
            h_text = _plain(_strip_attrs(h)).lower()
            if ep_idx is None and "episode" in h_text:
                ep_idx = j
            elif game_idx is None and ("challenge" in h_text or "mission" in h_text or "game" in h_text or "daily" in h_text):
                game_idx = j
            elif res_idx is None and ("result" in h_text or "outcome" in h_text):
                res_idx = j
        if res_idx is None:
            continue
        if ep_idx is None:
            ep_idx = 0
        if game_idx is None:
            game_idx = 1

        # Walk rows; handle rowspan
        pending = {}
        for row in data_rows:
            full = []
            target_len = max(res_idx, game_idx, ep_idx) + 1
            placed = 0
            col = 0
            row_cells = list(row)
            while placed < target_len:
                if col in pending:
                    full.append(pending[col][0])
                    pending[col] = (pending[col][0], pending[col][1] - 1)
                    if pending[col][1] <= 0:
                        del pending[col]
                else:
                    if not row_cells:
                        full.append("")
                        placed += 1
                        col += 1
                        continue
                    cell = row_cells.pop(0)
                    full.append(cell)
                    rs_m = re.search(r'\browspan\s*=\s*"?(\d+)"?', cell, re.IGNORECASE)
                    if rs_m and int(rs_m.group(1)) > 1:
                        pending[col] = (cell, int(rs_m.group(1)) - 1)
                placed += 1
                col += 1
            full.extend(row_cells)
            if len(full) <= max(res_idx, ep_idx, game_idx):
                continue
            ep = _plain(_strip_attrs(full[ep_idx]))
            game = _plain(_strip_attrs(full[game_idx]))
            res_cell = _strip_attrs(full[res_idx])
            res_text = _plain(res_cell).upper()

            if "WIN" in res_text:
                outcome = "W"
            elif "ELIM" in res_text or "OUT" in res_text:
                outcome = "L"
            elif "SAFE" in res_text:
                outcome = "S"
            else:
                outcome = "?"

            yield {
                "season_id": sid,
                "episode": ep,
                "game": game,
                "result": outcome,
            }


def build_fandom_daily_truth():
    season_map = build_season_map()
    rows = []
    for cf in sorted(CACHE.iterdir()):
        if not cf.is_file() or cf.stat().st_size == 0:
            continue
        wt = cf.read_text(encoding="utf-8")
        player = cf.stem.replace("_", " ")
        for rec in parse_player_dailies(wt, player, season_map):
            rows.append({
                "season_id": rec["season_id"],
                "player": player,
                "episode": rec["episode"],
                "game": rec["game"],
                "result": rec["result"],
            })
    return pd.DataFrame(rows)


def main():
    print("Parsing Fandom Challenge History tables...")
    truth = build_fandom_daily_truth()
    print(f"  {len(truth)} rows total, {(truth['result']=='W').sum()} WIN rows")
    print(f"  {truth['player'].nunique()} players across {truth['season_id'].nunique()} seasons")

    # Per (season_id, player) wins
    truth_wins = truth[truth["result"] == "W"].groupby(["season_id", "player"]).size().reset_index(name="fandom_wins")

    # Our data: count wins from dailies.csv per (season_id, winner)
    d = pd.read_csv(DATA / "dailies.csv")
    our_wins = d.groupby(["season_id", "winner"]).size().reset_index(name="our_wins")
    our_wins = our_wins.rename(columns={"winner": "player"})

    merged = pd.merge(truth_wins, our_wins, on=["season_id", "player"], how="outer").fillna(0)
    merged["fandom_wins"] = merged["fandom_wins"].astype(int)
    merged["our_wins"] = merged["our_wins"].astype(int)
    merged["diff"] = merged["our_wins"] - merged["fandom_wins"]

    # Focus on player-seasons where BOTH player has cached Fandom and there's
    # truth data for that season.
    cached = {cf.stem.replace("_", " ") for cf in CACHE.iterdir() if cf.is_file()}
    seasons_with_truth = set(truth["season_id"])
    mask = merged["player"].isin(cached) & merged["season_id"].isin(seasons_with_truth)
    filt = merged[mask]

    mismatches = filt[filt["diff"] != 0].sort_values(["season_id", "player"])
    print(f"\nPlayer-seasons with cached Fandom + truth: {len(filt)}")
    print(f"Mismatches: {len(mismatches)}  "
          f"(our > fandom: {(mismatches['diff']>0).sum()}, our < fandom: {(mismatches['diff']<0).sum()})")

    # Summary by season
    print("\nMismatch counts by season:")
    by_season = mismatches.groupby("season_id").agg(
        n_players=("player", "count"),
        sum_our_minus_fandom=("diff", "sum"),
        avg_abs_diff=("diff", lambda s: s.abs().mean()),
    ).round(2)
    print(by_season.to_string())

    merged.to_csv(DATA / "audit_dailies_per_player_season.csv", index=False)
    mismatches.to_csv(DATA / "audit_dailies_mismatches.csv", index=False)
    truth.to_csv(DATA / "audit_dailies_fandom_truth.csv", index=False)
    print(f"\nWrote audit CSVs to data/audit_dailies_*.csv")


if __name__ == "__main__":
    main()
