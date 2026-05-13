# =========================================================
# LAVIN — audit per-elim H2H records vs Fandom personal pages.
#
# For each player with a cached Fandom page, parse the per-season
# "===Elimination History===" tables. Headers vary by season:
#   4-col: Episode | Elimination | Opponent | Result
#   5-col: Episode | Elimination | Partner | Opponent(s) | Result
#   pair w/ rowspan: game column has rowspan=N for multi-round elims
#
# Strategy: read header row, identify the "Opponent" column by name,
# then use that index for each data row. Handle rowspan-merged cells
# (game cell shared across consecutive rows of the same elim).
#
# Output: diff report — what's in Fandom but not in our eliminations.csv,
# and what's in our data but not in Fandom.
# =========================================================
import re
from pathlib import Path
import pandas as pd
import mwparserfromhell as mwp

HERE = Path(__file__).parent
DATA = HERE / "data"
CACHE = DATA / ".fandom_player_cache"


def build_season_map():
    """
    Build {h2_title_lower → season_id}. Player-page h2 titles vary:
      - "The Challenge: Vets & New Threats" (verbatim page_name)
      - "Vets & New Threats" (page_name minus prefix)
      - "Vets and New Threats" (season_name with "and")
      - "Battle of the Seasons (2012)" (parenthetical year)
    """
    seasons = pd.read_csv(HERE / "seasons.csv")
    m = {}
    def add(k, sid):
        m[k.lower().strip()] = sid
    for _, r in seasons.iterrows():
        sid = r["season_id"]
        pn, sn = r["page_name"], r["season_name"]
        variants = set()
        for key in (pn, sn, pn.replace("_", " ")):
            variants.add(key)
            # Strip generic prefix (anything up to and including first colon)
            if ":" in key:
                variants.add(key.split(":", 1)[1].strip())
            # Strip parenthetical
            variants.add(re.sub(r"\s*\(.*?\)", "", key).strip())
        # Apply &/and swaps to all variants
        all_v = set(variants)
        for v in variants:
            all_v.add(v.replace(" & ", " and "))
            all_v.add(v.replace(" and ", " & "))
        for v in all_v:
            add(v, sid)
    return m


_OPP_HEADER_RE = re.compile(r"\bopponents?\b", re.IGNORECASE)
_RESULT_HEADER_RE = re.compile(r"\bresults?\b|\boutcomes?\b", re.IGNORECASE)


def _players_from_cell(cell_wt):
    if not cell_wt:
        return []
    out = []
    for m in re.finditer(
        r"\[\[(?:File|Image):[^|\]]+\|[^\]]*?link=([^|\]]+)[^\]]*\]\]", cell_wt
    ):
        out.append(m.group(1).strip())
    if not out:
        for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", cell_wt):
            t = m.group(1).strip()
            if not t.lower().startswith(("file:", "image:")):
                out.append(t)
    seen = set()
    uniq = []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def _strip_attrs(c):
    if "|" not in c:
        return c
    head, _, tail = c.partition("|")
    hl = head.strip().lower()
    if "=" in head or hl in ("align", "nowrap", "center", "rowspan", "colspan"):
        return tail
    return c


def _plain(c):
    return " ".join(mwp.parse(c).strip_code().split())


def _split_table_rows(table_text):
    """
    Split a wiki table into (header_cells, [data_rows]).

    Two wiki conventions for header placement:
      A) Headers immediately after `{|`:   `{|\n!H1\n!H2\n|-\n|D1...`
      B) Headers after first `|-`:          `{|\n|-\n!H1\n!H2\n|-\n|D1...`

    We detect both by looking at each `|-`-delimited chunk in order and
    treating the first chunk that contains `!`-prefixed cells as the
    header. Subsequent chunks (with `|`-prefixed cells) are data rows.
    """
    chunks = re.split(r"\n\|-", table_text)
    header_cells = []
    data_rows = []
    header_found = False
    for chunk in chunks:
        # Strip the leading `{|...` table-opening line (before any \n|- it
        # appears as part of chunk 0; we don't want it as a data cell).
        chunk_no_open = re.sub(r"^\s*\{\|[^\n]*\n", "", chunk)

        # Try to extract header cells from this chunk (lines starting with !)
        h_cells = []
        for line in chunk_no_open.split("\n"):
            s = line.strip()
            if s.startswith("!"):
                for h in s.lstrip("!").split("!!"):
                    h_cells.append(h.strip())
        if not header_found and h_cells:
            header_cells = h_cells
            header_found = True
            continue
        # Data row: split cell-by-cell on `\n|` or `\n!`. Use chunk_no_open
        # so the {| opener doesn't get parsed as a cell.
        # Also need to handle the case where the chunk starts directly with
        # a cell (no leading \n|), so prepend \n if needed.
        body = chunk_no_open
        if not body.startswith("\n"):
            body = "\n" + body
        cells = [c.strip() for c in re.split(r"\n[!|]", body) if c.strip()]
        if cells:
            data_rows.append(cells)
    return header_cells, data_rows


def _row_cell_count_with_rowspan(cell_text):
    """Return rowspan integer (default 1) for a cell."""
    m = re.search(r'\browspan\s*=\s*"?(\d+)"?', cell_text, re.IGNORECASE)
    return int(m.group(1)) if m else 1


def parse_player_elims(wt, player_name, season_map):
    """Yield (season_id, episode, game, opponents, result) for each row."""
    if not wt:
        return
    parts = re.split(r"\n==([^=][^=]*?)==\n", wt)
    for i in range(1, len(parts), 2):
        season_title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        em = re.search(
            r"===\s*Elimination History\s*===\s*\n(\{\|.*?\n\|\})", body, re.DOTALL
        )
        if not em:
            continue
        table_text = em.group(1)

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
        opp_idx = None
        res_idx = None
        for j, h in enumerate(header):
            h_text = _plain(_strip_attrs(h))
            if opp_idx is None and _OPP_HEADER_RE.search(h_text):
                opp_idx = j
            if res_idx is None and _RESULT_HEADER_RE.search(h_text):
                res_idx = j
        if opp_idx is None or res_idx is None:
            continue

        # Walk data rows, handling rowspan: track which columns are "filled
        # in" by a prior rowspan and which positions in current row are new.
        pending = {}  # col_idx -> (cell_text, remaining_rows)
        for row in data_rows:
            # Reconstruct full row by inserting pending rowspan cells.
            full_row = []
            row_iter = iter(row)
            col = 0
            row_cells = list(row)
            # For each column up to max needed
            target_len = max(opp_idx, res_idx) + 1
            placed = 0
            while placed < target_len:
                if col in pending:
                    full_row.append(pending[col][0])
                    pending[col] = (pending[col][0], pending[col][1] - 1)
                    if pending[col][1] <= 0:
                        del pending[col]
                else:
                    if not row_cells:
                        full_row.append("")
                        placed += 1
                        col += 1
                        continue
                    cell = row_cells.pop(0)
                    full_row.append(cell)
                    # Register rowspan for future rows
                    rs = _row_cell_count_with_rowspan(cell)
                    if rs > 1:
                        pending[col] = (cell, rs - 1)
                placed += 1
                col += 1
            # Append remaining original row cells
            full_row.extend(row_cells)

            if len(full_row) <= max(opp_idx, res_idx):
                continue
            ep_cell = _strip_attrs(full_row[0])
            game_cell = _strip_attrs(full_row[1]) if len(full_row) > 1 else ""
            opp_cell = _strip_attrs(full_row[opp_idx])
            res_cell = _strip_attrs(full_row[res_idx])

            opponents = _players_from_cell(opp_cell)
            res_players = _players_from_cell(res_cell)
            res_text = _plain(res_cell).upper()

            if "WIN" in res_text:
                result = "W"
            elif "OUT" in res_text or "LOSS" in res_text:
                result = "L"
            elif player_name in res_players:
                result = "W"
            elif res_players:
                result = "L"
            else:
                low = res_cell.lower()
                if any(c in low for c in ("tomato", "firebrick", "salmon", "lightcoral")):
                    result = "L"
                elif any(c in low for c in ("navy", "blue", "greenyellow", "deepskyblue", "lightgreen", "mediumblue")):
                    result = "W"
                else:
                    result = "?"

            yield {
                "season_id": sid,
                "episode": _plain(ep_cell),
                "game": _plain(game_cell),
                "opponents": opponents,
                "result": result,
            }


def build_fandom_truth():
    season_map = build_season_map()
    truth = []
    for cf in sorted(CACHE.iterdir()):
        if not cf.is_file() or cf.stat().st_size == 0:
            continue
        wt = cf.read_text(encoding="utf-8")
        player = cf.stem.replace("_", " ")
        for rec in parse_player_elims(wt, player, season_map):
            for opp in rec["opponents"]:
                if opp == player:  # skip self-references
                    continue
                truth.append({
                    "season_id": rec["season_id"],
                    "player": player,
                    "episode": rec["episode"],
                    "game": rec["game"],
                    "opponent": opp,
                    "fandom_result": rec["result"],
                })
    return pd.DataFrame(truth)


def diff_vs_ours(truth):
    """
    Compare same-gender H2H entries only. Mixed-gender cross-product elims
    we emit for pair-format seasons aren't typically tracked on Fandom
    personal pages (Fandom lists opponents you faced in your gender's
    Arena, partner-pair attribution is implicit), so they'd skew the diff.
    """
    e = pd.read_csv(DATA / "eliminations.csv")
    players = pd.read_csv(DATA / "players.csv")
    gmap = dict(zip(players["player"], players["gender"]))

    ours_set = set()
    for _, r in e.iterrows():
        w, l, sid = r.get("winner"), r.get("loser"), r["season_id"]
        if not (isinstance(w, str) and isinstance(l, str)):
            continue
        if gmap.get(w) != gmap.get(l):  # filter mixed-gender cross-product
            continue
        ours_set.add((sid, w, l, "W"))
        ours_set.add((sid, l, w, "L"))

    truth_set = set()
    for _, r in truth.iterrows():
        if r["fandom_result"] not in ("W", "L"):
            continue
        if gmap.get(r["player"]) != gmap.get(r["opponent"]):
            continue
        truth_set.add((r["season_id"], r["player"], r["opponent"], r["fandom_result"]))

    return truth_set - ours_set, ours_set - truth_set, truth_set, ours_set


def main():
    print("Parsing Fandom personal pages...")
    truth = build_fandom_truth()
    print(f"  {len(truth)} per-opponent rows from {truth['player'].nunique()} players "
          f"(covering {truth['season_id'].nunique()} seasons)")

    missing, extra, truth_set, ours_set = diff_vs_ours(truth)

    # Filter to player pairs where BOTH have a cached Fandom page
    cached = {cf.stem.replace("_", " ") for cf in CACHE.iterdir() if cf.is_file()}
    missing_filt = [m for m in missing if m[1] in cached and m[2] in cached]
    extra_filt = [m for m in extra if m[1] in cached and m[2] in cached]

    print(f"\n=== Diff (filtered to player pairs with both Fandom pages cached) ===")
    print(f"  Missing in ours (Fandom has, we don't): {len(missing_filt)}")
    print(f"  Extra in ours   (we have, Fandom doesn't): {len(extra_filt)}")

    from collections import Counter
    print("\n  Missing by season:")
    for sid, n in sorted(Counter(m[0] for m in missing_filt).items()):
        print(f"    {sid}: {n}")
    print("\n  Extra by season:")
    for sid, n in sorted(Counter(m[0] for m in extra_filt).items()):
        print(f"    {sid}: {n}")

    pd.DataFrame(missing_filt, columns=["season_id", "player", "opponent", "fandom_result"])\
      .sort_values(["season_id", "player"]).to_csv(DATA / "audit_elim_missing.csv", index=False)
    pd.DataFrame(extra_filt, columns=["season_id", "player", "opponent", "our_result"])\
      .sort_values(["season_id", "player"]).to_csv(DATA / "audit_elim_extra.csv", index=False)
    truth.to_csv(DATA / "audit_elim_fandom_truth.csv", index=False)

    print(f"\nWrote audit CSVs to data/audit_elim_*.csv")


if __name__ == "__main__":
    main()
