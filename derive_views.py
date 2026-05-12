# =========================================================
# LAVIN — derive the three player views (PEAK / ERA / ACTIVE) from the
# single solver's per-snapshot rating timeline.
#
# Fleet pattern (mirrors LOBO/DUNCAN/ZIDANE): one solver outputs ratings
# at every ranking_id. We then construct multiple presentational views
# from that timeline rather than running multiple solvers.
#
#   PEAK   = max rating ever achieved by the player (best single moment)
#   ACTIVE = rating at the player's most recent snapshot (current/final form)
#   ERA    = cumulative sum of POSITIVE rating contribution across snapshots
#            (career stature; rewards quality × quantity / longevity)
#
# Why "cumulative positive sum" for ERA and not just mean:
#   - Mean rating treats a 1-season hot streak the same as a 10-season
#     career of consistent quality. ERA needs to reward longevity.
#   - Cumulative sum rewards both quality (per-snapshot rating magnitude)
#     and quantity (number of snapshots the player held that rating).
#   - "Positive" floor ignores below-average snapshots so a player's
#     decline years don't erase their prime years.
#   - Units: roughly "rating-units × snapshots above average." A score
#     of 200 means roughly "held a +1.0 rating for 200 snapshots."
# =========================================================
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"

# Optional min-snapshots floor for PEAK ranking (keeps a 1-snapshot wonder
# out of the top-10 PEAK list). ERA uses cumulative-sum so it naturally
# discounts thin-sample players.
PEAK_MIN_SNAPSHOTS = 30


def main():
    ratings = pd.read_csv(DATA / "ratings_lavin" / "ratings.csv")

    print(f"Loaded {len(ratings):,} per-snapshot rating rows.")
    print(f"  unique players: {ratings['player'].nunique()}")

    # PEAK — max rating any player ever held
    peak = (
        ratings.sort_values("rating", ascending=False)
        .groupby("player")
        .head(1)
        .rename(columns={"rating": "peak_rating",
                         "ranking_id": "peak_ranking_id",
                         "season_id": "peak_season_id"})
        [["player", "gender", "peak_rating", "peak_ranking_id", "peak_season_id"]]
    )

    # ACTIVE — rating at most recent snapshot the player appears in
    active = (
        ratings.sort_values("ranking_id", ascending=True)
        .groupby("player")
        .tail(1)
        .rename(columns={"rating": "active_rating",
                         "ranking_id": "active_ranking_id",
                         "season_id": "active_season_id"})
        [["player", "active_rating", "active_ranking_id", "active_season_id"]]
    )

    # ERA — sum of POSITIVE end-of-season ratings across the seasons the
    # player actually PLAYED. Critical filter: by default the WLS solver
    # publishes a rating at every snapshot where a player's events are
    # within the rolling window (so a player keeps "earning" ratings for
    # ~6 seasons after their last appearance as their events age out).
    # Without this filter we'd accumulate "ghost ERA" from seasons the
    # player wasn't even cast in.
    import pandas as _pd
    apps = _pd.read_csv(DATA / "appearances.csv")
    played = set(zip(apps["player"].astype(str), apps["season_id"].astype(str)))

    end_of_season = (
        ratings.sort_values("ranking_id")
        .groupby(["player", "season_id"]).tail(1)
    )
    # Keep only EOS rows for (player, season) the player actually played
    mask = [(p, s) in played for p, s in zip(end_of_season["player"].astype(str),
                                              end_of_season["season_id"].astype(str))]
    eos_played = end_of_season[mask]
    pos_eos = eos_played[eos_played["rating"] > 0]
    era_pos = (
        pos_eos.groupby("player")["rating"].sum()
        .rename("era_rating")
        .reset_index()
    )
    era_counts = (
        ratings.groupby("player")
        .agg(era_n_snapshots=("rating", "count"))
        .reset_index()
    )
    era = era_counts.merge(era_pos, on="player", how="left")
    era["era_rating"] = era["era_rating"].fillna(0.0)

    # Join into one player-level view
    views = peak.merge(active, on="player").merge(era, on="player")
    views = views.sort_values("peak_rating", ascending=False)
    views.to_csv(DATA / "player_views.csv", index=False)
    print(f"\nWrote data/player_views.csv ({len(views)} players)")

    # Top lists
    elig_peak = views[views["era_n_snapshots"] >= PEAK_MIN_SNAPSHOTS]

    for g, label in [("M", "MEN"), ("F", "WOMEN")]:
        print(f"\n=== TOP 10 ERA — {label} (career stature) ===")
        for _, row in views[views["gender"] == g].sort_values("era_rating", ascending=False).head(10).iterrows():
            print(f"  {row['player']:25s}  era={row['era_rating']:+6.1f}  peak={row['peak_rating']:+.2f}  active={row['active_rating']:+.2f}  snaps={int(row['era_n_snapshots']):4d}")

        print(f"\n=== TOP 10 PEAK — {label} (best single moment, ≥ {PEAK_MIN_SNAPSHOTS} snaps) ===")
        for _, row in elig_peak[elig_peak["gender"] == g].sort_values("peak_rating", ascending=False).head(10).iterrows():
            print(f"  {row['player']:25s}  peak={row['peak_rating']:+.2f}  era={row['era_rating']:+6.1f}  active={row['active_rating']:+.2f}  snaps={int(row['era_n_snapshots']):4d}")


if __name__ == "__main__":
    main()
