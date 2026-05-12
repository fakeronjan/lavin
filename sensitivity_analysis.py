# =========================================================
# LAVIN — sensitivity analysis on the 4 event-type weights.
#
# Runs 13 variants of the WLS solver and compares Top 20 ERA per gender:
#
#   baseline          — current weights (final_field 0.10, others 1.0)
#   no_dailies        — daily weight = 0
#   no_eliminations   — elim weight = 0
#   no_finals_within  — finals_within weight = 0
#   no_finals_field   — finals_field weight = 0  (effectively current * 0)
#   dailies_0.5x      — half weight on dailies
#   dailies_2x        — double weight on dailies
#   elims_0.5x        — half weight on elims
#   elims_2x          — double weight on elims
#   within_0.5x       — half weight on finals_within
#   within_2x         — double weight on finals_within
#   field_0.5x        — finals_field scale 0.05 (half of 0.10)
#   field_2x          — finals_field scale 0.20 (double of 0.10)
#
# Output: data/sensitivity.html — side-by-side Top 20 comparison.
# =========================================================
import json
from pathlib import Path

import pandas as pd

from lavin import compute_ratings, annotate_events, build_snapshot_meta

HERE = Path(__file__).parent
DATA = HERE / "data"

WINDOW = 90
EOS_ONLY = True
DECAY = False

# Baseline type-scales (final_field already discounted at 0.10)
BASE = {
    "elimination":  1.0,
    "daily":        1.0,
    "final_within": 1.0,
    "final_field":  0.10,
}

# Variants
VARIANTS = {
    "baseline":         dict(BASE),
    # Targeted rebalance proposals
    "user_proposal":    {"elimination": 1.5, "daily": 1.0, "final_within": 0.5, "final_field": 0.20},  # within 1.0 ÷ 2 = 0.5 multiplier; events already 2x
    "equal_weight":     {"elimination": 1.0, "daily": 1.24, "final_within": 0.54, "final_field": 0.31},
    "middle_ground":    {"elimination": 1.25, "daily": 1.1, "final_within": 0.7, "final_field": 0.25},
    # Knockouts
    "no_dailies":       {**BASE, "daily": 0.0},
    "no_eliminations":  {**BASE, "elimination": 0.0},
    "no_finals_within": {**BASE, "final_within": 0.0},
    "no_finals_field":  {**BASE, "final_field": 0.0},
}

# Same "EOS positive sum" logic as derive_views.py
def compute_era_from_ratings(ratings_df, appearances):
    played = set(zip(appearances["player"].astype(str), appearances["season_id"].astype(str)))
    end_of_season = (
        ratings_df.sort_values("ranking_id")
        .groupby(["player", "season_id"]).tail(1)
    )
    mask = [(p, s) in played for p, s in zip(end_of_season["player"].astype(str),
                                              end_of_season["season_id"].astype(str))]
    eos_played = end_of_season[mask]
    pos_eos = eos_played[eos_played["rating"] > 0]
    era = pos_eos.groupby("player")["rating"].sum().reset_index().rename(columns={"rating": "era"})
    return era


def main():
    print("Loading inputs...")
    events = pd.read_csv(DATA / "events.csv")
    players = pd.read_csv(DATA / "players.csv")
    appearances = pd.read_csv(DATA / "appearances.csv")
    gmap = dict(zip(players["player"].astype(str), players["gender"].astype(str)))

    events = annotate_events(events)
    snap_meta = build_snapshot_meta(events)
    print(f"  {len(events)} events\n")

    # Run each variant
    results = {}
    for name, type_scales in VARIANTS.items():
        print(f"=== {name} ===  scales = {type_scales}")
        ratings = compute_ratings(
            events, gmap,
            window_size=WINDOW,
            recency_decay=DECAY,
            eos_only=EOS_ONLY,
            type_scales=type_scales,
        )
        ratings = ratings.merge(snap_meta, on="ranking_id", how="left")
        era = compute_era_from_ratings(ratings, appearances)
        era["gender"] = era["player"].map(gmap)
        results[name] = era
        # Save the raw ratings file too for inspection
        out_dir = DATA / f"ratings_sens_{name}"
        out_dir.mkdir(exist_ok=True)
        ratings.to_csv(out_dir / "ratings.csv", index=False)

    # Build comparison HTML
    print("\nBuilding comparison HTML...")
    write_comparison_html(results, gmap)
    print(f"Wrote {DATA / 'sensitivity.html'}")


def rank_dict(era_df, gender, top_n=20):
    """Return {player: rank} for top-N of a gender."""
    sub = era_df[era_df["gender"] == gender].sort_values("era", ascending=False).head(top_n)
    return {row["player"]: (i + 1, row["era"]) for i, (_, row) in enumerate(sub.iterrows())}


def write_comparison_html(results, gmap):
    # Reference order: baseline top 20 per gender
    baseline = results["baseline"]
    sections = []
    for gender, label in [("M", "Men"), ("F", "Women")]:
        base_rank = rank_dict(baseline, gender, top_n=20)
        # Union of players in top 20 across all variants
        union_players = set()
        per_variant_ranks = {}
        for name, era in results.items():
            r = rank_dict(era, gender, top_n=20)
            per_variant_ranks[name] = r
            union_players.update(r.keys())
        # Order rows by baseline rank, then by largest movement across variants
        ordered = sorted(
            union_players,
            key=lambda p: base_rank.get(p, (99, 0))[0]
        )

        # Header
        variants_list = list(VARIANTS.keys())
        head = "<th class='player-col'>Player</th>" + "".join(
            f"<th>{v}</th>" for v in variants_list
        )

        # Rows
        rows = []
        for p in ordered:
            cells = [f"<td class='player-col'>{p}</td>"]
            for v in variants_list:
                rk = per_variant_ranks[v].get(p)
                if rk:
                    delta = (rk[0] - base_rank.get(p, (99, 0))[0]) if v != "baseline" else 0
                    arrow = ""
                    if v != "baseline" and delta:
                        arrow_str = "▲" if delta < 0 else "▼"
                        arrow_color = "#2e7d32" if delta < 0 else "#c62828"
                        arrow = f" <span style='color:{arrow_color};font-size:10px'>{arrow_str}{abs(delta)}</span>"
                    cells.append(f"<td class='num'>#{rk[0]}{arrow}</td>")
                else:
                    cells.append("<td class='empty'>—</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        sections.append(f"""
        <section>
          <h2>{label}</h2>
          <table>
            <thead><tr>{head}</tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </section>
        """)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LAVIN — Sensitivity Analysis</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f8f8f6; color: #1a1a1a; padding: 24px; max-width: 1800px; margin: 0 auto; font-size: 13px; }}
  h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
  h2 {{ font-size: 18px; margin: 28px 0 10px 0; padding-bottom: 6px; border-bottom: 2px solid #1a6b8a; }}
  .legend {{ background: #fff; border: 1px solid #ddd; padding: 14px 16px;
            border-radius: 8px; margin-bottom: 20px; font-size: 13px; line-height: 1.5; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
          box-shadow: 0 1px 3px rgba(0,0,0,0.07); font-size: 12px; }}
  thead th {{ background: #1a6b8a; color: #ff6eb4; padding: 8px 6px; text-align: left;
             font-weight: 700; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px;
             white-space: nowrap; }}
  th.player-col {{ text-align: left; min-width: 180px; }}
  tbody td {{ padding: 6px 8px; border-bottom: 1px solid #eee; }}
  td.player-col {{ font-weight: 600; }}
  td.num {{ text-align: center; font-variant-numeric: tabular-nums; }}
  td.empty {{ text-align: center; color: #ccc; }}
</style>
</head>
<body>
<h1>LAVIN — Sensitivity Analysis</h1>
<div class="legend">
  <p><strong>Methodology:</strong> baseline runs the EOS-only solver with current weights
     (elim 1.0 / daily 1.0 / finals_within 1.0 / finals_field 0.10). Each other variant
     changes ONE weight while holding the rest constant. ERA = sum of positive
     end-of-season ratings for the seasons the player actually played.</p>
  <p><strong>Reading:</strong> each cell shows the player's rank under that variant.
     <span style="color:#2e7d32">▲N</span> = moved up N spots vs baseline.
     <span style="color:#c62828">▼N</span> = moved down N spots vs baseline.
     "—" = fell out of the top 20.</p>
  <p><strong>What to look for:</strong> who drops out of top 20 when <em>dailies</em> are zeroed?
     They're daily-driven. Who's unaffected by zeroing <em>finals_within</em>? Their rating
     doesn't lean on championship victories. Etc.</p>
</div>
{''.join(sections)}
</body>
</html>
"""
    (DATA / "sensitivity.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
