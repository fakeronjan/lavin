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


if __name__ == "__main__":
    main()
