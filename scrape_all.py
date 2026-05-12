# =========================================================
# LAVIN — scrape all seasons from seasons.csv
# =========================================================
import time
import sys
import traceback
from pathlib import Path

import pandas as pd

from scrape_fandom import scrape_season

HERE = Path(__file__).parent
SEASONS_CSV = HERE / "seasons.csv"
OUT = HERE / "data" / "raw"


def main():
    seasons = pd.read_csv(SEASONS_CSV)
    results = []
    print(f"Scraping {len(seasons)} seasons...")
    print()

    for _, row in seasons.iterrows():
        sid = row["season_id"]
        page = row["page_name"]
        try:
            r = scrape_season(sid, page, OUT)
            r["status"] = "ok"
            print(f"  {sid:45s} contestants={r['contestants']:3d}  elims={r['eliminations']:3d}  dailies={r['dailies']:3d}")
        except Exception as e:
            r = {
                "season_id": sid, "page_name": page,
                "wikitext_bytes": 0, "contestants": 0,
                "eliminations": 0, "dailies": 0,
                "status": f"ERROR: {type(e).__name__}: {e}",
            }
            print(f"  {sid:45s} ERROR: {type(e).__name__}: {e}")
        results.append(r)
        time.sleep(0.5)  # courtesy delay to Fandom API

    df = pd.DataFrame(results)
    df.to_csv(HERE / "data" / "scrape_summary.csv", index=False)
    print()
    print("=" * 80)
    print(f"Successful: {(df['status']=='ok').sum()} / {len(df)}")
    print(f"Mean contestants per season: {df['contestants'].mean():.1f}")
    print(f"Total eliminations: {df['eliminations'].sum()}")
    print(f"Total dailies: {df['dailies'].sum()}")
    errors = df[df['status'] != 'ok']
    if len(errors):
        print()
        print("FAILED:")
        for _, e in errors.iterrows():
            print(f"  {e['season_id']:45s} {e['status']}")


if __name__ == "__main__":
    main()
