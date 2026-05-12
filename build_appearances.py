# =========================================================
# LAVIN — build the consolidated CSVs from per-season raw output.
#   appearances.csv : one row per (season, player) with gender + finish
#   eliminations.csv : flat file of every H2H elim across all seasons
#   dailies.csv     : flat file of every daily win across all seasons
#   players.csv     : master list of unique players with gender
#
# Gender attribution:
#   1) Anywhere a player appears in eliminations.csv, that row's `gender`
#      column is authoritative (the duel is gender-segregated).
#   2) Cast-table captions ("Men"/"Women"/"Male contestants"/etc.) give
#      gender to players who never went to elim.
#   3) Any players still missing gender are reported for manual fix-up.
# =========================================================
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
RAW = HERE / "data" / "raw"
OUT = HERE / "data"


def load_all_seasons():
    """Return dict {season_id: {'contestants': df, 'eliminations': df, 'dailies': df}}."""
    out = {}
    for sd in sorted(RAW.iterdir()):
        if not sd.is_dir():
            continue
        sid = sd.name
        rec = {}
        for kind in ("contestants", "eliminations", "dailies"):
            f = sd / f"{kind}.csv"
            rec[kind] = pd.DataFrame()
            if f.exists() and f.stat().st_size > 2:
                try:
                    rec[kind] = pd.read_csv(f)
                except pd.errors.EmptyDataError:
                    pass
        out[sid] = rec
    return out


def build_gender_map(all_data):
    """
    Authoritative gender per player.

    Priority order:
      1. CAST captions (Male/Female/Men/Women tables) — unambiguous label of
         the cast-table half a player appears in.
      2. ELIM row gender — only as fallback. Reason: in team-format seasons
         (Battle of the Sexes 2, etc.) the elim chart's "gender" column
         labels which TEAM won the daily, not the elim contestants' gender.
         Trusting it as primary mislabels female players as male.

    Returns ({player: 'M'|'F'}, conflicts_list).
    """
    gmap = {}
    conflicts = []

    # Pass 1: cast captions (high confidence)
    for sid, rec in all_data.items():
        c = rec["contestants"]
        if not len(c) or "gender" not in c.columns:
            continue
        for _, row in c.iterrows():
            p = row.get("player")
            if not isinstance(p, str) or not p.strip():
                continue
            p = p.strip()
            g = str(row.get("gender") or "").strip() if not pd.isna(row.get("gender")) else ""
            if g not in ("M", "F"):
                continue
            prev = gmap.get(p)
            if prev and prev != g:
                conflicts.append((p, prev, g, f"cast:{sid}"))
            else:
                gmap[p] = g

    # Pass 2: elim row gender for players still missing
    for sid, rec in all_data.items():
        e = rec["eliminations"]
        if not len(e) or "gender" not in e.columns:
            continue
        for _, row in e.iterrows():
            g = str(row.get("gender") or "").strip() if not pd.isna(row.get("gender")) else ""
            if g not in ("M", "F"):
                continue
            for p in (row.get("winner"), row.get("loser")):
                if not isinstance(p, str) or not p.strip():
                    continue
                p = p.strip()
                if p in gmap:
                    continue  # already known from cast
                gmap[p] = g
    return gmap, conflicts


def fold_in_cast_gender(all_data, gmap):
    """Backwards-compat shim — cast gender already folded into the first pass."""
    return 0


def fold_in_overrides(gmap):
    """
    Pass 3: read data/gender_overrides.csv (fetched from Fandom categories)
    and fill in players still missing gender. These overrides are the
    authoritative answer for team-format-only players whose cast tables
    have team captions and elim charts have no gender column.
    """
    overrides_path = OUT / "gender_overrides.csv"
    if not overrides_path.exists():
        return 0
    df = pd.read_csv(overrides_path)
    filled = 0
    for _, row in df.iterrows():
        p = str(row.get("player") or "").strip()
        g = str(row.get("gender") or "").strip()
        if not p or g not in ("M", "F"):
            continue
        if p not in gmap:
            gmap[p] = g
            filled += 1
    return filled


def build_appearances(all_data, gmap):
    """One row per (season, player). Combines cast + event participants."""
    rows = []
    for sid, rec in all_data.items():
        season_players = {}  # player -> {finish, source}

        # 1) Seed from contestants table (has finish info)
        c = rec["contestants"]
        if len(c):
            for _, row in c.iterrows():
                p = (row.get("player") or "").strip()
                if not p:
                    continue
                season_players[p] = {
                    "finish": str(row.get("finish") or "").strip() if not pd.isna(row.get("finish")) else "",
                    "origin": str(row.get("origin") or "").strip() if not pd.isna(row.get("origin")) else "",
                    "source": "cast",
                }

        # 2) Add players from elims (winners + losers) — fills cast parser gaps
        e = rec["eliminations"]
        if len(e):
            for _, row in e.iterrows():
                for p in (row.get("winner"), row.get("loser")):
                    if isinstance(p, str) and p.strip() and p.strip() not in season_players:
                        season_players[p.strip()] = {
                            "finish": "",
                            "origin": "",
                            "source": "elim_only",
                        }

        # 3) Add players from dailies — fills gaps for players who only won dailies
        d = rec["dailies"]
        if len(d):
            for _, row in d.iterrows():
                p = row.get("winner")
                if isinstance(p, str) and p.strip() and p.strip() not in season_players:
                    season_players[p.strip()] = {
                        "finish": "",
                        "origin": "",
                        "source": "daily_only",
                    }

        for player, info in season_players.items():
            rows.append({
                "season_id": sid,
                "player": player,
                "gender": gmap.get(player, ""),
                "finish": info["finish"],
                "origin": info["origin"],
                "source": info["source"],
            })
    return pd.DataFrame(rows)


def main():
    print("Loading raw data...")
    all_data = load_all_seasons()
    print(f"  {len(all_data)} seasons loaded")

    print("\nBuilding gender map from eliminations...")
    gmap, conflicts = build_gender_map(all_data)
    print(f"  {len(gmap)} players gendered via elim data")
    if conflicts:
        print(f"  WARNING: {len(conflicts)} gender conflicts (same player labeled both M and F):")
        for p, prev, new, sid in conflicts[:10]:
            print(f"    {p}: {prev}→{new} in {sid}")

    print("\nFolding in cast-table gender for non-elim players...")
    filled = fold_in_cast_gender(all_data, gmap)
    print(f"  +{filled} players gendered via cast caption")

    print("\nApplying Fandom-category overrides for team-only players...")
    filled_ovr = fold_in_overrides(gmap)
    print(f"  +{filled_ovr} players gendered via Fandom category")
    print(f"  Total: {len(gmap)} players with gender")

    print("\nBuilding appearances...")
    appearances = build_appearances(all_data, gmap)
    print(f"  {len(appearances)} (season, player) rows")
    by_source = appearances["source"].value_counts()
    print(f"  by source: {by_source.to_dict()}")
    missing = appearances[appearances["gender"] == ""]
    pct = 100 * len(missing) / len(appearances)
    print(f"  missing gender: {len(missing)} / {len(appearances)} ({pct:.1f}%)")

    # Concat all eliminations and dailies into flat files
    all_elims = pd.concat(
        [rec["eliminations"] for rec in all_data.values() if len(rec["eliminations"])],
        ignore_index=True,
    )
    all_dailies = pd.concat(
        [rec["dailies"] for rec in all_data.values() if len(rec["dailies"])],
        ignore_index=True,
    )

    appearances.to_csv(OUT / "appearances.csv", index=False)
    all_elims.to_csv(OUT / "eliminations.csv", index=False)
    all_dailies.to_csv(OUT / "dailies.csv", index=False)

    # Players master list
    players = appearances.groupby("player").agg(
        gender=("gender", lambda s: next((g for g in s if g), "")),
        seasons=("season_id", "nunique"),
    ).reset_index().sort_values("seasons", ascending=False)
    players.to_csv(OUT / "players.csv", index=False)

    print("\nWrote:")
    print(f"  data/appearances.csv  ({len(appearances)} rows)")
    print(f"  data/eliminations.csv ({len(all_elims)} rows)")
    print(f"  data/dailies.csv      ({len(all_dailies)} rows)")
    print(f"  data/players.csv      ({len(players)} unique players)")

    if len(missing):
        sample = missing["player"].drop_duplicates().head(20)
        print(f"\nSample of players still missing gender ({missing['player'].nunique()} unique):")
        for p in sample:
            print(f"  {p}")


if __name__ == "__main__":
    main()
