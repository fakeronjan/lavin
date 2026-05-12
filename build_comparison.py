# =========================================================
# LAVIN — END-OF-SEASON top-10 snapshots across configs
# Output: data/comparison.html
#
# For each "showcase" season, show the top 10 men + women at the season's
# final ranking_id, under each of the 6 configs side-by-side.
# This is the proper face-validity view — does the model put the right
# players at the top of the era's rankings at the END of that season?
# =========================================================
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = DATA / "comparison.html"
TOP_N = 3
MIN_EVENTS = 0   # rely on solver's per-player gate; don't filter further here

CONFIGS = [
    ("lavin",   "End-of-season rating"),
]

# Showcase seasons: S10 → S30 inclusive (user-requested wide era sweep)
SHOWCASE_SEASONS = [
    ("s10_the_inferno_ii",                  "S10 Inferno II (2005)"),
    ("s11_the_gauntlet_2",                  "S11 Gauntlet 2 (2005)"),
    ("s12_fresh_meat",                       "S12 Fresh Meat (2006)"),
    ("s13_the_duel",                         "S13 The Duel (2006)"),
    ("s14_the_inferno_3",                    "S14 Inferno 3 (2007)"),
    ("s15_the_gauntlet_iii",                 "S15 Gauntlet III (2008)"),
    ("s16_the_island",                       "S16 The Island (2008)"),
    ("s17_the_duel_ii",                      "S17 Duel II (2009)"),
    ("s18_the_ruins",                        "S18 The Ruins (2009)"),
    ("s19_fresh_meat_ii",                    "S19 Fresh Meat II (2010)"),
    ("s20_cutthroat",                        "S20 Cutthroat (2010)"),
    ("s21_rivals",                           "S21 Rivals (2011)"),
    ("s22_battle_of_the_exes",               "S22 Battle of the Exes (2012)"),
    ("s23_battle_of_the_seasons_2012",       "S23 Battle of the Seasons (2012)"),
    ("s24_rivals_ii",                        "S24 Rivals II (2013)"),
    ("s25_free_agents",                      "S25 Free Agents (2014)"),
    ("s26_battle_of_the_exes_ii",            "S26 Battle of the Exes II (2015)"),
    ("s27_battle_of_the_bloodlines",         "S27 Battle of the Bloodlines (2015)"),
    ("s28_rivals_iii",                       "S28 Rivals III (2016)"),
    ("s29_invasion_of_the_champions",        "S29 Invasion of the Champions (2017)"),
    ("s30_xxx_dirty_30",                     "S30 XXX: Dirty 30 (2017)"),
]

TOUCHSTONES_M = {
    "Johnny Bananas", "CT Tamburello", "Wes Bergmann", "Darrell Taylor",
    "Mark Long", "Evan Starkman", "Kenny Santucci", "Jordan Wiseley",
    "Landon Lueck", "Brad Fiorenza", "Derrick Kosinski", "Cory Wharton",
    "Tony Raines", "Devin Walker", "Kyle Christie", "Frank Roessler",
    "Theo Campbell", "Nehemiah Clark", "Big Easy Banks", "Tyler Duckworth",
    "Eric Nies", "Mike Mizanin", "Abram Boise", "Alton Williams",
    "Dustin Zito", "Zach Nichols",
}
TOUCHSTONES_F = {
    "Evelyn Smith", "Laurel Stucky", "Cara Maria Sorbello", "Aneesa Ferreira",
    "Sarah Rice", "Susie Meister", "Veronica Portillo", "Rachel Robinson",
    "Paula Meronek", "Jenny West", "Jodi Weatherton", "Kam Williams",
    "Cara Zavaleta", "Diem Brown", "Coral Smith", "Kaycee Clark",
    "Robin Hibbard", "Svetlana Shusterman", "Beth Stolarczyk",
    "Tina Barta", "Aviv Melmed", "Casey Cooper",
}


_appearances_cache = None


def _load_appearances():
    global _appearances_cache
    if _appearances_cache is None:
        _appearances_cache = pd.read_csv(DATA / "appearances.csv")
    return _appearances_cache


def season_end_top(cfg, season_id, gender, top_n=TOP_N):
    """
    Return top players at the LAST ranking_id of `season_id`, filtered to
    players who actually competed in that season. This is the proper face-
    validity view — "after season X concluded, who's top among the cast?"
    """
    r = pd.read_csv(DATA / f"ratings_{cfg}" / "ratings.csv")
    sub = r[(r["season_id"] == season_id) & (r["gender"] == gender)]
    if not len(sub):
        return pd.DataFrame()
    last_rid = sub["ranking_id"].max()
    snap = sub[(sub["ranking_id"] == last_rid) & (sub["n_events"] >= MIN_EVENTS)].copy()
    # Filter to season participants
    apps = _load_appearances()
    season_players = set(apps[apps["season_id"] == season_id]["player"].astype(str))
    snap = snap[snap["player"].isin(season_players)]
    snap = snap.sort_values("rating", ascending=False).head(top_n)
    snap["rank"] = range(1, len(snap) + 1)
    return snap


def build_cell(row, touchstones):
    if row.empty:
        return "<td class='empty'>—</td>"
    cls = ' class="touchstone"' if row["player"] in touchstones else ""
    return (
        f'<td{cls}>'
        f'<div class="name">{row["player"]}</div>'
        f'<div class="meta">{row["rating"]:+.2f} · {int(row["n_events"])} ev</div>'
        f'</td>'
    )


def build_season_table(season_id, season_label, gender, gender_label, touchstones):
    cells_by_cfg = {cfg: season_end_top(cfg, season_id, gender) for cfg, _ in CONFIGS}
    rows = []
    for i in range(1, TOP_N + 1):
        rows.append("<tr>")
        rows.append(f'<td class="rank-col">#{i}</td>')
        for cfg, _ in CONFIGS:
            df = cells_by_cfg[cfg]
            if i <= len(df):
                row = df.iloc[i - 1]
                rows.append(build_cell(row, touchstones))
            else:
                rows.append("<td class='empty'>—</td>")
        rows.append("</tr>")
    headers = "".join(f"<th>{lbl}</th>" for _, lbl in CONFIGS)
    return f"""
    <h3>{gender_label}</h3>
    <table>
      <thead><tr><th class="rank-col">#</th>{headers}</tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def main():
    sections = []
    for season_id, season_label in SHOWCASE_SEASONS:
        sections.append(f'<section><h2>{season_label}</h2>')
        sections.append(build_season_table(season_id, season_label, "M", "Men", TOUCHSTONES_M))
        sections.append(build_season_table(season_id, season_label, "F", "Women", TOUCHSTONES_F))
        sections.append("</section>")

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LAVIN — End-of-Season Top 10 Comparison</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #fafafa; color: #222; padding: 24px; max-width: 1600px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
    h2 {{ font-size: 18px; margin: 32px 0 12px 0; padding-bottom: 6px; border-bottom: 2px solid #2c3e50; }}
    h3 {{ font-size: 13px; margin: 12px 0 6px 0; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }}
    .subtitle {{ color: #666; margin-bottom: 16px; font-size: 13px; }}
    .legend {{ background: #fff; border: 1px solid #ddd; padding: 10px 14px;
               border-radius: 6px; margin-bottom: 20px; font-size: 12px; }}
    .legend .touchstone-sample {{ background: #fff7d6; padding: 2px 5px; border-radius: 3px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
             box-shadow: 0 1px 2px rgba(0,0,0,0.06); table-layout: fixed; margin-bottom: 8px; }}
    th {{ background: #2c3e50; color: #fff; padding: 6px 8px; text-align: left;
          font-size: 11px; font-weight: 600; }}
    th.rank-col {{ width: 36px; text-align: center; }}
    td {{ padding: 5px 8px; border-bottom: 1px solid #eee; vertical-align: top;
          font-size: 12px; overflow: hidden; }}
    td.rank-col {{ text-align: center; color: #888; font-weight: 600; }}
    td.touchstone {{ background: #fff7d6; }}
    td.empty {{ color: #ccc; text-align: center; }}
    td .name {{ font-weight: 600; }}
    td .meta {{ font-size: 10px; color: #666; font-variant-numeric: tabular-nums; }}
    section {{ margin-bottom: 36px; }}
  </style>
</head>
<body>
  <h1>LAVIN — End-of-Season Top 10</h1>
  <p class="subtitle">
    At each showcased season's <strong>finals snapshot</strong>, the top 10 men and women
    by rating. Each section compares all 6 (window × finals_field) configs side-by-side.
  </p>
  <div class="legend">
    <strong>What to look for:</strong>
    Does the right player rise to the top at the right era? E.g.,
    Landon dominating mid-2000s, Bananas in his peak 2010s back-to-back run,
    Wiseley in his Free Agents/Rivals III run, the most recent stars in S41.
    <br>
    <span class="touchstone-sample">Yellow rows</span> = touchstone players (era-relevant legends).
    <br>
    Each cell: player name · rating · events-in-window.
  </div>
  {''.join(sections)}
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  Open: file://{OUT.absolute()}")


if __name__ == "__main__":
    main()
