# =========================================================
# LAVIN — fetch gender from Fandom for players we can't infer otherwise.
#
# Some players only competed in team-format seasons where cast tables
# have team captions (not Male/Female) and elim charts have no gender
# column. Their gender stays NaN through the normal attribution pipeline.
#
# Fix: each player has a Fandom page tagged with categories like
# "Male_Contestants" or "Female_Contestants". One API call per player.
#
# Output: data/gender_overrides.csv (player, gender, source)
# build_appearances.py picks this up as a final attribution pass.
# =========================================================
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
DATA = HERE / "data"
API = "https://thechallenge.fandom.com/api.php"
UA = "lavin-research/0.1 (rjsikdar@gmail.com)"

OUT = DATA / "gender_overrides.csv"


def fetch_gender(player_name):
    """Return 'M', 'F', or '' for a player based on Fandom page categories."""
    page = player_name.replace(" ", "_")
    try:
        r = requests.get(
            API,
            params={"action": "parse", "page": page, "format": "json",
                    "prop": "categories", "redirects": "true"},
            headers={"User-Agent": UA},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            return ""
        cats = [c.get("*", "") for c in j.get("parse", {}).get("categories", [])]
        cats_set = set(cats)
        if "Male_Contestants" in cats_set or "Male_contestants" in cats_set:
            return "M"
        if "Female_Contestants" in cats_set or "Female_contestants" in cats_set:
            return "F"
        return ""
    except Exception as e:
        print(f"  ! {player_name}: {type(e).__name__}: {e}")
        return ""


def main():
    # Load existing gender info
    players = pd.read_csv(DATA / "players.csv")
    appearances = pd.read_csv(DATA / "appearances.csv")
    elims = pd.read_csv(DATA / "eliminations.csv")
    dailies = pd.read_csv(DATA / "dailies.csv")

    gmap = dict(zip(players["player"].astype(str), players["gender"].fillna("").astype(str)))

    # Event-relevant players who are missing gender
    event_players = set(elims["winner"].dropna()) | set(elims["loser"].dropna()) | set(dailies["winner"].dropna())
    event_players = {x for x in event_players if isinstance(x, str)}
    missing = sorted([p for p in event_players if not gmap.get(p)])

    print(f"Fetching gender for {len(missing)} players (event-relevant, missing gender)...")
    print(f"Estimated time: {len(missing) * 1.1:.0f}s\n")

    # Reuse any prior file
    prior = {}
    if OUT.exists():
        for _, row in pd.read_csv(OUT).iterrows():
            prior[row["player"]] = row["gender"]
        print(f"  Loaded {len(prior)} prior overrides; skipping those.\n")

    results = list(prior.items())
    for i, p in enumerate(missing, 1):
        if p in prior:
            continue
        g = fetch_gender(p)
        results.append((p, g))
        marker = "✓" if g in ("M", "F") else "?"
        print(f"  [{i:3d}/{len(missing)}] {marker} {p:35s} → {g or '<not found>'}")
        time.sleep(0.5)  # courtesy delay

    df = pd.DataFrame(results, columns=["player", "gender"])
    df["source"] = "fandom_category"
    df = df[df["gender"].isin(["M", "F"])]
    df.to_csv(OUT, index=False)
    print(f"\nWrote {OUT} ({len(df)} overrides)")


if __name__ == "__main__":
    main()
