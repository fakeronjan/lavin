# =========================================================
# LAVIN — resolve Fandom redirects for every player name in our data,
# so duplicates like Frank Fox / Frank Sweeney merge into one identity.
#
# For each player, query Fandom's API. If the page redirects to a
# different title, that title is the canonical name. Write
# data/aliases.csv (alias, canonical) for downstream consumers.
# =========================================================
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
DATA = HERE / "data"
API = "https://thechallenge.fandom.com/api.php"
UA = "lavin-research/0.1 (rjsikdar@gmail.com)"

OUT = DATA / "aliases.csv"


def resolve_canonical(name):
    """
    Return canonical Fandom page title for `name`, or '' if not found.
    Uses ?action=query&redirects=1 — if there's a redirect, the response
    includes `query.redirects` listing the redirect chain and `query.pages`
    keyed by the canonical title.
    """
    page = name.replace(" ", "_")
    try:
        r = requests.get(
            API,
            params={
                "action": "query", "titles": page,
                "redirects": "true", "format": "json",
            },
            headers={"User-Agent": UA},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        pages = j.get("query", {}).get("pages", {})
        for pid, info in pages.items():
            if pid == "-1":
                return ""  # page doesn't exist
            return info.get("title", "")
    except Exception as e:
        print(f"  ! {name}: {type(e).__name__}: {e}")
        return ""
    return ""


def main():
    players = pd.read_csv(DATA / "players.csv")
    names = sorted(set(players["player"].astype(str)))
    print(f"Resolving canonical Fandom titles for {len(names)} players...")
    print(f"Estimated time: {len(names) * 0.6:.0f}s\n")

    prior = {}
    if OUT.exists():
        for _, row in pd.read_csv(OUT).iterrows():
            prior[row["alias"]] = row["canonical"]
        print(f"  Loaded {len(prior)} prior aliases; skipping those.\n")

    aliases = []
    for prev_alias, prev_canon in prior.items():
        aliases.append((prev_alias, prev_canon))

    n_new = 0
    for i, name in enumerate(names, 1):
        if name in prior:
            continue
        canonical = resolve_canonical(name)
        if canonical and canonical != name:
            aliases.append((name, canonical))
            n_new += 1
            print(f"  [{i:3d}/{len(names)}] ALIAS  {name:30s} → {canonical}")
        elif canonical:
            # Already canonical — no need to record
            pass
        else:
            # Page doesn't exist — record so we don't re-query
            print(f"  [{i:3d}/{len(names)}] ?      {name:30s} (no Fandom page)")
        time.sleep(0.4)

    df = pd.DataFrame(aliases, columns=["alias", "canonical"])
    df.to_csv(OUT, index=False)
    print(f"\nWrote {len(df)} aliases total ({n_new} new this run) to {OUT}")


if __name__ == "__main__":
    main()
