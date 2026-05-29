# =========================================================
# LAVIN — apply cleanups to all derived data files in place:
#   1. Player aliases (Frank Sweeney → Frank Fox, etc.)
#   2. Drop rows with malformed player names (icon syntax leaks, non-player
#      strings like "Champs vs. Pros" that came from edge-case cells)
#   3. Drop rows where loser/winner is empty/NaN
#
# Re-run build_appearances + build_events after this to consolidate.
# =========================================================
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"

# Regex matching obvious parser-leak artifacts in name fields:
#   "50px|link=Player Name", "linkPlayer", trailing template scraps, etc.
MALFORMED_RE = re.compile(r"50px\|link=|^link=|<br|\{\{|\}\}")

# Rows that appear in elimination charts but are not actually eliminations.
# The scraper sees the player icons in these ceremonial rows and emits
# phantom elim events; this set drops them post-scrape without
# complicating the scraper's heuristics (which would risk false-positives
# on Inferno-format rows that legitimately trail with darkgray colspan
# placeholders).
# Format: {(season_id, episode_str): reason_comment}
NON_ELIM_ROWS = {
    ("s11_the_gauntlet_2", "1"): "Royal Rumble captain-selection ceremony (both men's + women's brackets — Adam L vs Alton and Jo vs Ruthie were captain candidates, not Gauntlet contestants)",
}

# Verified elimination corrections (vs Fandom + season wikitext). Each applies
# to the per-season raw eliminations.csv; build_appearances.py re-aggregates.
#
# DROP_ELIM_PAIRS: (season, winner, loser) rows to remove. Used where our
#   scraper paired two CO-LOSERS of the same multi-team elimination as if one
#   beat the other (verified: both show "Eliminated in <same episode>").
# ADD_ELIM_ROWS: verified (season, episode, gender, game, winner, loser) rows
#   our scraper missed — the actual winner-vs-each-loser pairs.
# FLIP_ELIM_PAIRS: (season, recorded_winner, recorded_loser) to swap, where we
#   recorded the result backwards.
DROP_ELIM_PAIRS = {
    # S33 ep4* was a double team elimination: Kyle's team beat CT+JP ("The
    # Greatest Showman"), Mattie's team beat Julia+Natalie ("It's Complicated").
    # CT/JP and Julia/Natalie were co-losers, not winner-vs-loser.
    ("s33_war_of_the_worlds", "CT Tamburello", "JP Andrade"),
    ("s33_war_of_the_worlds", "Julia Nolan", "Natalie Negrotti"),
}
ADD_ELIM_ROWS = [
    ("s33_war_of_the_worlds", "4*", "M", "The Greatest Showman", "Kyle Christie", "CT Tamburello"),
    ("s33_war_of_the_worlds", "4*", "M", "The Greatest Showman", "Kyle Christie", "JP Andrade"),
    ("s33_war_of_the_worlds", "4*", "F", "It's Complicated", "Mattie Lynn Breaux", "Julia Nolan"),
    ("s33_war_of_the_worlds", "4*", "F", "It's Complicated", "Mattie Lynn Breaux", "Natalie Negrotti"),
]
# S39's "Conquest" finale format produces ambiguous elimination semantics
# (a player can lose a Conquest round yet still advance), so single
# winner/loser attribution there is unreliable on both our side AND Fandom's.
# Left uncorrected pending a proper Conquest-format reconstruction.
FLIP_ELIM_PAIRS = set()

# Names that look like player names but are clearly not players (season titles,
# franchise names, etc.) that have leaked into player-name columns.
NON_PLAYER_NAMES = {
    "Champs vs. Pros",
    "The Challenge",
    "Real World/Road Rules Challenge",
}


def load_aliases():
    p = DATA / "aliases.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    return dict(zip(df["alias"].astype(str), df["canonical"].astype(str)))


def clean_name_series(series, aliases):
    """Apply aliases, drop malformed/non-player entries (returns Series with NaN for drops)."""
    def clean(x):
        if not isinstance(x, str):
            return x
        s = x.strip()
        if not s:
            return None
        if s in NON_PLAYER_NAMES:
            return None
        if MALFORMED_RE.search(s):
            return None
        return aliases.get(s, s)
    return series.map(clean)


def cleanup_file(path, name_cols, aliases, drop_cols_for_invalid):
    """
    Apply aliases to specified name columns. Rows where ANY of `drop_cols_for_invalid`
    becomes None/NaN are dropped (means we can't use the row).
    Returns (rows_kept, rows_dropped).
    """
    df = pd.read_csv(path)
    n_before = len(df)
    for col in name_cols:
        if col in df.columns:
            df[col] = clean_name_series(df[col], aliases)
    if drop_cols_for_invalid:
        mask = df[drop_cols_for_invalid].notna().all(axis=1) & (df[drop_cols_for_invalid] != "").all(axis=1)
        df = df[mask]
    df.to_csv(path, index=False)
    return len(df), n_before - len(df)


def main():
    aliases = load_aliases()
    print(f"Loaded {len(aliases)} aliases\n")

    # appearances.csv — keep all rows even if name becomes invalid (might be source='cast')
    # But drop rows where the canonical player name is empty after cleaning.
    kept, drop = cleanup_file(
        DATA / "appearances.csv",
        name_cols=["player"],
        aliases=aliases,
        drop_cols_for_invalid=["player"],
    )
    print(f"  appearances.csv:  kept {kept}, dropped {drop}")

    # eliminations.csv — both winner and loser need to be valid
    kept, drop = cleanup_file(
        DATA / "eliminations.csv",
        name_cols=["winner", "loser"],
        aliases=aliases,
        drop_cols_for_invalid=["winner", "loser"],
    )
    print(f"  eliminations.csv: kept {kept}, dropped {drop}")

    # dailies.csv — only winner is required
    kept, drop = cleanup_file(
        DATA / "dailies.csv",
        name_cols=["winner"],
        aliases=aliases,
        drop_cols_for_invalid=["winner"],
    )
    print(f"  dailies.csv:      kept {kept}, dropped {drop}")

    # Also clean per-season raw files so re-running build_appearances picks up clean data
    raw_dir = DATA / "raw"
    raw_kept = 0
    raw_dropped = 0
    for season_dir in sorted(raw_dir.iterdir()):
        if not season_dir.is_dir():
            continue
        for kind, name_cols, required in [
            ("contestants.csv", ["player"], ["player"]),
            ("eliminations.csv", ["winner", "loser"], ["winner", "loser"]),
            ("dailies.csv", ["winner"], ["winner"]),
        ]:
            f = season_dir / kind
            if not f.exists() or f.stat().st_size < 3:
                continue
            try:
                k, d = cleanup_file(f, name_cols, aliases, required)
                raw_kept += k
                raw_dropped += d
            except pd.errors.EmptyDataError:
                pass
    print(f"  per-season raw:   kept {raw_kept}, dropped {raw_dropped}")

    # Drop known non-elim ceremonial rows (e.g. S11 ep 1 Royal Rumble)
    # from per-season eliminations.csv. The aggregated data/eliminations.csv
    # is regenerated by build_appearances.py downstream.
    non_elim_dropped = 0
    for (sid, ep) in NON_ELIM_ROWS:
        f = raw_dir / sid / "eliminations.csv"
        if f.exists() and f.stat().st_size > 3:
            df = pd.read_csv(f)
            before = len(df)
            df = df[df["episode"].astype(str) != ep]
            if len(df) < before:
                df.to_csv(f, index=False)
                non_elim_dropped += before - len(df)
    if non_elim_dropped:
        print(f"  non-elim filter:  dropped {non_elim_dropped} ceremonial rows")

    # Apply verified elim corrections (drop co-loser pairs, flip reversed
    # results, add missed winner-vs-loser rows) to per-season raw files.
    corr_drop = corr_flip = corr_add = 0
    seasons_touched = {s for s, *_ in DROP_ELIM_PAIRS} | {s for s, *_ in FLIP_ELIM_PAIRS} | {r[0] for r in ADD_ELIM_ROWS}
    for sid in seasons_touched:
        f = raw_dir / sid / "eliminations.csv"
        if not (f.exists() and f.stat().st_size > 3):
            continue
        df = pd.read_csv(f)
        for (s, w, l) in DROP_ELIM_PAIRS:
            if s != sid:
                continue
            m = (df["winner"] == w) & (df["loser"] == l)
            corr_drop += int(m.sum()); df = df[~m]
        for i, row in df.iterrows():
            if (sid, row["winner"], row["loser"]) in FLIP_ELIM_PAIRS:
                df.at[i, "winner"], df.at[i, "loser"] = row["loser"], row["winner"]
                corr_flip += 1
        new_rows = [{"season_id": s, "episode": ep, "gender": g, "game": gm, "winner": w, "loser": l}
                    for (s, ep, g, gm, w, l) in ADD_ELIM_ROWS if s == sid]
        if new_rows:
            df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
            corr_add += len(new_rows)
        df.to_csv(f, index=False)
    if corr_drop or corr_flip or corr_add:
        print(f"  elim corrections: dropped {corr_drop} co-loser, flipped {corr_flip}, added {corr_add}")


if __name__ == "__main__":
    main()
