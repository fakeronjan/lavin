# =========================================================
# LAVIN — audit each player's championship count vs Fandom truth.
#
# Reads cached Fandom wikitext (data/.fandom_player_cache/),
# parses the InfoboxChallenger `challenges` field, and counts entries
# that match "(won)" — those are the player's championship-winning seasons.
#
# Filters to MAIN-SERIES seasons only (excludes Champs vs. Pros, All Stars,
# UK Challenge, Spies Lies & Allies of Sweden, etc. — anything not in the
# main Challenge canon S5-S41 we model).
#
# Output: data/audit_championships.csv — one row per player with mismatches.
# =========================================================
import re
from pathlib import Path

import pandas as pd
import mwparserfromhell as mwp

HERE = Path(__file__).parent
DATA = HERE / "data"
CACHE = DATA / ".fandom_player_cache"

# Substrings that mark a season as NOT in our main-series scope.
NON_MAIN_SERIES_MARKERS = [
    "champs vs", "all stars", "spies, lies & allies of sweden",
    "uk challenge", "uk vs the world", "world championship",
]


def is_main_series(season_link_title):
    s = season_link_title.lower()
    if any(m in s for m in NON_MAIN_SERIES_MARKERS):
        return False
    return True


def parse_challenges_field(wt):
    """
    From a player's wikitext, parse the InfoboxChallenger `challenges`
    field. Return list of (season_title, status) where status is one of
    "won", "final", "appearance", "host", etc.
    """
    if not wt:
        return []
    code = mwp.parse(wt)
    for tpl in code.filter_templates():
        tname = str(tpl.name).strip().lower()
        if "infobox" in tname and "challenger" in tname:
            for p in tpl.params:
                if str(p.name).strip().lower() == "challenges":
                    return _parse_challenges_value(str(p.value))
    return []


def _parse_challenges_value(value):
    """
    The `challenges` field is HTML/wiki mix like:
      '''''[[Season Name|Display]]''''' (won)<br>
      [[Other Season]] (final)<br>
      ...

    Returns list of (canonical_season_name, status_lowercase).
    """
    out = []
    # Split on <br> for each entry
    for line in re.split(r"<br\s*/?>", value, flags=re.IGNORECASE):
        line = line.strip()
        if not line:
            continue
        # Match the full wikilink (with optional |display segment) so we can
        # skip past it before looking for status. Otherwise year-
        # disambiguation parentheses INSIDE the link target get parsed as
        # the status (e.g. "Battle of the Seasons (2012)" → year inside).
        link_m = re.search(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", line)
        if not link_m:
            continue
        season = link_m.group(1).strip()
        # Status is parenthesized text AFTER the wikilink closes
        after = line[link_m.end():]
        status_m = re.search(r"\(([^)]+)\)", after)
        status = status_m.group(1).strip().lower() if status_m else "appearance"
        out.append((season, status))
    return out


def main():
    appearances = pd.read_csv(DATA / "appearances.csv")
    players = pd.read_csv(DATA / "players.csv")

    # Our derived championship counts from finish text
    our_champs = {}
    for _, row in appearances.iterrows():
        p = str(row.get("player") or "").strip()
        finish = str(row.get("finish") or "")
        if not p:
            continue
        if re.match(r"^Winners?\b", finish, re.IGNORECASE):
            our_champs[p] = our_champs.get(p, 0) + 1

    rows = []
    for _, prow in players.iterrows():
        name = prow["player"]
        cache_file = CACHE / f"{name.replace(' ', '_').replace('/', '_')}.txt"
        wt = cache_file.read_text(encoding="utf-8") if cache_file.exists() else ""
        challenges = parse_challenges_field(wt)
        f_main = [c for c in challenges if is_main_series(c[0])]
        f_wins = [c for c in f_main if c[1] == "won"]
        f_main_count = len(f_main)
        f_win_count = len(f_wins)

        our_count = our_champs.get(name, 0)
        diff = our_count - f_win_count

        if diff != 0:
            rows.append({
                "player": name,
                "gender": prow["gender"],
                "our_championships": our_count,
                "fandom_main_championships": f_win_count,
                "fandom_main_appearances": f_main_count,
                "diff": diff,
                "missing_seasons_in_ours": "; ".join(s for s, st in f_wins) if f_win_count > our_count else "",
            })

    df = pd.DataFrame(rows).sort_values("diff")
    df.to_csv(DATA / "audit_championships.csv", index=False)
    print(f"Wrote {DATA / 'audit_championships.csv'}")
    print()
    print(f"Players with championship-count mismatch: {len(df)}")
    print()
    print("=== Players where Fandom shows MORE wins than us (missing championships) ===")
    missing = df[df["diff"] < 0].copy()
    print(missing.head(30).to_string(index=False))
    print()
    print("=== Players where WE show MORE wins than Fandom (likely false-positive) ===")
    extras = df[df["diff"] > 0].copy()
    print(extras.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
