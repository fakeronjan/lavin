# =========================================================
# LAVIN — per-dimension ERA breakdown.
#
# Runs the WLS solver 4 extra times, each isolating ONE event type
# (other 3 zeroed). For each player, computes their EOS-positive-sum
# from that isolated run. Result: per-player breakdown of how much of
# their career rating leans on dailies vs eliminations vs making
# finals vs winning finals.
#
# CAVEAT: these dimension ERAs are NOT additive — WLS is non-linear and
# events interact through the regression. But they're directionally
# meaningful as "this player is daily-driven" / "elim-driven" signals.
#
# Output: data/dimension_eras.csv with columns
#   player, gender, era_total, era_daily, era_elim, era_within, era_field
# =========================================================
from pathlib import Path

import pandas as pd

from lavin import compute_ratings, annotate_events, build_snapshot_meta

HERE = Path(__file__).parent
DATA = HERE / "data"

WINDOW = 60
EOS_ONLY = True
DECAY = True  # 4-season linear-decay window (2026-05-12 tuning, see lavin.py)

# Total scales — committed equal-weight tuning
TOTAL_SCALES = {
    "elimination":  1.0,
    "daily":        1.24,
    "final_within": 0.54,
    "final_field":  0.31,
}

# Component variants — Nx the chosen dimension, keep OTHERS at baseline.
# This keeps the full event set in the regression (vs. zeroing-out which
# makes data sparse and produces weird ratings for thin-sample players).
# A player whose rating rises sharply under one of these is "leveraged"
# on that dimension; one whose rating barely moves is dimension-agnostic.
#
# Boost calibration (2026-05-12): 10x chosen over 4x after side-by-side
# comparison. At 4x the %-spread across dimensions averaged 12pts for
# men / 17pts for women; at 10x it's 18pts / 25pts. Specialists like
# Leroy (43% elim), Cory Wharton (35% made-final), Ashley Mitchell
# (48% won-final), Rachel Robinson (49% won-final) become visually
# obvious instead of muted. The "Bananas/CT great at everything"
# pattern still holds (they top every absolute sort) but their
# %-breakdowns now show CT as more championship-leaning, Wiseley as
# elim-leaning, etc.
BOOST = 10.0
COMPONENT_VARIANTS = {
    "daily":        {**TOTAL_SCALES, "daily":        TOTAL_SCALES["daily"]        * BOOST},
    "elim":         {**TOTAL_SCALES, "elimination":  TOTAL_SCALES["elimination"]  * BOOST},
    "final_within": {**TOTAL_SCALES, "final_within": TOTAL_SCALES["final_within"] * BOOST},
    "final_field":  {**TOTAL_SCALES, "final_field":  TOTAL_SCALES["final_field"]  * BOOST},
}


def era_from_ratings(ratings_df, snap_meta, played_set):
    """Sum of positive end-of-season ratings, filtered to seasons player actually played."""
    ratings_df = ratings_df.merge(snap_meta, on="ranking_id", how="left")
    eos = ratings_df.sort_values("ranking_id").groupby(["player", "season_id"]).tail(1)
    mask = [(p, s) in played_set for p, s in
            zip(eos["player"].astype(str), eos["season_id"].astype(str))]
    pos = eos[mask][eos[mask]["rating"] > 0]
    return pos.groupby("player")["rating"].sum()


def main():
    print("Loading inputs...")
    events = pd.read_csv(DATA / "events.csv")
    players = pd.read_csv(DATA / "players.csv")
    apps = pd.read_csv(DATA / "appearances.csv")
    gmap = dict(zip(players["player"].astype(str), players["gender"].astype(str)))
    played_set = set(zip(apps["player"].astype(str), apps["season_id"].astype(str)))

    events_ann = annotate_events(events)
    snap_meta = build_snapshot_meta(events_ann)
    print(f"  {len(events_ann)} events\n")

    # Run total (committed) + 4 isolations
    eras = {}
    print("=== total (committed equal-weight) ===")
    r = compute_ratings(
        events_ann, gmap, window_size=WINDOW, recency_decay=DECAY,
        eos_only=EOS_ONLY, type_scales=TOTAL_SCALES,
    )
    eras["total"] = era_from_ratings(r, snap_meta, played_set)

    for name, scales in COMPONENT_VARIANTS.items():
        print(f"=== {name}_4x ===")
        r = compute_ratings(
            events_ann, gmap, window_size=WINDOW, recency_decay=DECAY,
            eos_only=EOS_ONLY, type_scales=scales,
        )
        eras[name] = era_from_ratings(r, snap_meta, played_set)

    # Stitch into per-player DataFrame
    all_players = sorted(set().union(*[set(s.index) for s in eras.values()]))
    rows = []
    for p in all_players:
        rows.append({
            "player": p,
            "gender": gmap.get(p, ""),
            "era_total":  round(float(eras["total"].get(p, 0)), 2),
            "era_daily":  round(float(eras["daily"].get(p, 0)), 2),
            "era_elim":   round(float(eras["elim"].get(p, 0)), 2),
            "era_within": round(float(eras["final_within"].get(p, 0)), 2),
            "era_field":  round(float(eras["final_field"].get(p, 0)), 2),
        })
    df = pd.DataFrame(rows)
    df.to_csv(DATA / "dimension_eras.csv", index=False)
    print(f"\nWrote {DATA / 'dimension_eras.csv'} ({len(df)} players)")

    # Print a quick top-10 by each dimension for sanity
    for col, label in [("era_total", "Total"), ("era_daily", "Daily"),
                       ("era_elim", "Elim"), ("era_within", "Within (champ)"),
                       ("era_field", "Field (made final)")]:
        print(f"\n=== Top 5 MEN by {label} ===")
        top = df[df["gender"] == "M"].sort_values(col, ascending=False).head(5)
        for _, r in top.iterrows():
            print(f"  {r['player']:25s}  {col}={r[col]:+5.1f}")


if __name__ == "__main__":
    main()
