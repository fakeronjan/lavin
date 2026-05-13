# =========================================================
# LAVIN — WLS ratings for The Challenge
#
# Pattern adapted from DUNCAN's _solve_massey:
#   - X (n_events × n_players) with +1 for winner, -1 for loser
#   - y = 1.0 (binary "win margin" — every event row records a victory)
#   - W = base_event_weight × recency_factor
#   - Zero-sum constraint as a high-weight extra row (centers ratings at 0)
#   - WLS via row-scaling: solve (sqrt(W) X) r = sqrt(W) y by lstsq
#
# Cadence (see project_lavin_ranking_cadence memory):
#   ranking_id ticks once per elimination, not per TV episode. Each elim is
#   one snapshot. Finals of each season add one extra "end-of-season"
#   snapshot. Dailies between two elims fold into the next elim's window.
#
# Outputs (under data/ratings_<config>/):
#   ratings.csv  (ranking_id, season_id, elim_idx, player, gender, rating, n_events)
#   snapshots.csv (ranking_id metadata)
# =========================================================
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------
# Within an episode, dailies come BEFORE the elimination they precede,
# and finals come last in the season. This ordering matters when building
# rolling snapshots — daily events fold into the next elim's window.
EVENT_TYPE_ORDER = {"daily": 0, "elimination": 1, "final": 2}

# Window × finals_field-scale matrix. Each config is (window_size, final_field_scale).
# Window units: number of elims; None = no cap. final_field_scale multiplies the
# baseline weight on `final_field` events (finalist beats each non-finalist).
CONFIGS = {
    # End-of-season-only solver. We display per-season ratings; computing
    # the WLS at every elim was producing within-season noise nobody saw.
    # Now: one rating per (player, season-end-snapshot).
    #
    # Settings (2026-05-12 tuning — replaced window=90/no-decay):
    #   - 4-season window (~60 elims back from each season-end)
    #   - Linear recency decay: events at the snapshot have weight 1.0,
    #     events at the window edge have weight ~0. Effective per-season
    #     weights are roughly 40/30/20/10 across the 4 seasons, which is
    #     what the user's "1-2-3-4" weighting concept asked for — current-
    #     season signal dominates instead of being diluted across 6 flat
    #     seasons (the old 90/flat had this season at only ~17% of weight).
    #   - "Equal-weight" type_scales: target ~25% of total weight per
    #     dimension. final_within was 47% of total weight at the baseline
    #     2.0 per-event; cutting to ~0.54 brings it in line.
    #
    # Side-by-side comparison on S19-S22 validated the change: the
    # Bananas-eliminated-by-CT case (S20) properly drops him from #3 to
    # #9, Dunbar's Red-Team-winner status surfaces (#6 → #2), and the
    # top-of-top players (Bananas/CT/Wiseley/Wes; Cara Maria/Laurel/
    # Tori/Ashley) remain stable.
    "lavin":  {
        "window": 60, "decay": True, "eos_only": True,
        "type_scales": {
            "elimination":  1.0,
            "daily":        1.24,
            "final_within": 0.54,
            "final_field":  0.31,
        },
    },
}

MIN_EVENTS_PER_PLAYER = 5        # minimum events before a player's rating is published
ZERO_SUM_WEIGHT = 1.0e8          # weight on the rating-mean-zero constraint row


# ---------------------------------------------------------
# Event annotation
# ---------------------------------------------------------
def annotate_events(events):
    """
    Add bookkeeping columns:
      snum            integer season number (for sort order)
      type_ord        within-episode event ordering (daily < elim < final)
      elim_idx_global monotonic int — the snapshot a row belongs to.
                      For elim rows: the elim's own snapshot.
                      For daily rows: the snapshot of the NEXT elim in the
                                      same season.
                      For final rows: a virtual "season-end" snapshot one
                                      tick past the last elim of the season.
    Events returned sorted by (snum, ep_ord, type_ord).
    """
    events = events.copy()
    events["snum"] = events["season_id"].str.extract(r"s(\d+)_").astype(int)
    events["type_ord"] = events["event_type"].map(EVENT_TYPE_ORDER).fillna(0).astype(int)
    events = events.sort_values(["snum", "ep_ord", "type_ord"]).reset_index(drop=True)

    # Assign elim_idx_global per row
    n = len(events)
    elim_idx = np.zeros(n, dtype=int)
    global_counter = 0

    for sid in events["season_id"].unique():
        s_mask = events["season_id"] == sid
        s_indices = np.where(s_mask)[0]
        s_types = events.loc[s_indices, "event_type"].values

        # First pass: assign each elim its own monotonic index
        pending = []  # indices of non-elim events awaiting the next elim
        for local_i, idx in enumerate(s_indices):
            t = s_types[local_i]
            if t == "elimination":
                global_counter += 1
                elim_idx[idx] = global_counter
                # Back-fill any pending daily/final events with this elim's idx
                for p in pending:
                    elim_idx[p] = global_counter
                pending = []
            else:
                pending.append(idx)
        # Pending non-elim events after the season's last elim (finals).
        # Give them a virtual "season-end" snapshot one tick past the last elim.
        if pending:
            global_counter += 1
            for p in pending:
                elim_idx[p] = global_counter

    events["elim_idx_global"] = elim_idx
    return events


def build_snapshot_meta(events):
    """One row per snapshot: (ranking_id, season_id, elim_idx_in_season, is_finals)."""
    rows = []
    for sid, sdf in events.groupby("season_id"):
        for eig, esub in sdf.groupby("elim_idx_global"):
            types = set(esub["event_type"])
            rows.append({
                "ranking_id": int(eig),
                "season_id": sid,
                "is_finals": ("final" in types) and ("elimination" not in types),
            })
    df = pd.DataFrame(rows).sort_values("ranking_id").reset_index(drop=True)
    # Compute elim_idx_in_season for display
    df["elim_idx_in_season"] = df.groupby("season_id").cumcount() + 1
    return df


# ---------------------------------------------------------
# WLS solver (adapted from DUNCAN._solve_massey)
# ---------------------------------------------------------
def solve_wls(window_events):
    """
    Solve for player ratings on a single window of pre-weighted events.
    `window_events` must have columns: player_a, player_b, effective_weight.
    Returns {player: rating}.
    """
    players = sorted(set(window_events["player_a"]) | set(window_events["player_b"]))
    if len(players) < 2:
        return {}
    p_idx = {p: i for i, p in enumerate(players)}
    n_p, n_e = len(players), len(window_events)

    X = np.zeros((n_e + 1, n_p))
    y = np.zeros(n_e + 1)
    w = np.zeros(n_e + 1)

    pa = window_events["player_a"].map(p_idx).to_numpy()
    pb = window_events["player_b"].map(p_idx).to_numpy()
    wt = window_events["effective_weight"].to_numpy(dtype=float)

    rows = np.arange(n_e)
    X[rows, pa] = 1.0
    X[rows, pb] = -1.0
    y[:n_e] = 1.0  # binary "+1" win margin
    w[:n_e] = wt

    # Mean-zero constraint
    X[-1, :] = 1.0
    y[-1] = 0.0
    w[-1] = ZERO_SUM_WEIGHT

    sqrt_w = np.sqrt(w)
    Xw = X * sqrt_w[:, None]
    yw = y * sqrt_w
    r, *_ = np.linalg.lstsq(Xw, yw, rcond=None)
    return dict(zip(players, r))


# ---------------------------------------------------------
# Top-level: compute ratings across all snapshots for one config
# ---------------------------------------------------------
def compute_ratings(events, gender_map, window_size,
                    field_scale=1.0, recency_decay=True, eos_only=False,
                    type_scales=None):
    """
    Iterate per snapshot. At each, take events in the window, apply recency
    weighting AND per-event-type scales, run WLS separately for M and F,
    emit per-player rating rows.

    eos_only=True: only compute at the END-OF-SEASON snapshot for each
    season (the final ranking_id in each season). Skips per-elim mid-season
    snapshots that we never display anyway. ~20x faster, no within-season
    noise in the rating timeline.

    type_scales: optional dict {event_type: multiplier} for sensitivity
    analysis. Defaults to {"final_field": field_scale} which preserves
    existing behavior. Set any event_type to 0 to knock it out entirely.
    """
    events = events.copy()
    events["gender"] = events["player_a"].map(gender_map)
    # Sanity: drop any cross-gender rows (build_events shouldn't emit them, but be safe)
    events = events[events["player_a"].map(gender_map) == events["player_b"].map(gender_map)]

    # Per-event-type scaling applied here so we don't rebuild events.csv per config.
    if type_scales is None:
        type_scales = {"final_field": field_scale}
    scale_map = events["event_type"].map(lambda t: type_scales.get(t, 1.0))
    events = events.assign(weight=events["weight"] * scale_map)

    snapshots = sorted(events["elim_idx_global"].unique())
    if eos_only:
        # Keep only the last snapshot of each season — that's our EOS point.
        last_per_season = (
            events.sort_values("elim_idx_global")
            .groupby("season_id")["elim_idx_global"].max()
        )
        snapshots = sorted(last_per_season.unique())
    print(f"  {len(snapshots)} snapshots to compute")

    out_rows = []
    for k in snapshots:
        # Window: events in (k - window_size, k] by elim_idx_global
        if window_size is None:
            window = events[events["elim_idx_global"] <= k]
        else:
            lo = k - window_size
            window = events[(events["elim_idx_global"] > lo) & (events["elim_idx_global"] <= k)]
        if not len(window):
            continue

        window = window.copy()
        if recency_decay and window_size is not None:
            elims_ago = (k - window["elim_idx_global"]).to_numpy()
            window["recency_factor"] = (window_size - elims_ago) / window_size
        else:
            window["recency_factor"] = 1.0
        window["effective_weight"] = window["weight"] * window["recency_factor"]

        for gender in ("M", "F"):
            g_win = window[window["gender"] == gender]
            if not len(g_win):
                continue
            ratings = solve_wls(g_win)
            # Count events per player for min-events gate
            counts = pd.concat([g_win["player_a"], g_win["player_b"]]).value_counts()
            for player, rating in ratings.items():
                n = int(counts.get(player, 0))
                if n < MIN_EVENTS_PER_PLAYER:
                    continue
                out_rows.append({
                    "ranking_id": int(k),
                    "player": player,
                    "gender": gender,
                    "rating": float(rating),
                    "n_events": n,
                })
    return pd.DataFrame(out_rows)


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
def main():
    print("Loading events + players...")
    events = pd.read_csv(DATA / "events.csv")
    players = pd.read_csv(DATA / "players.csv")
    gender_map = dict(zip(players["player"].astype(str), players["gender"].astype(str)))

    events = annotate_events(events)
    snap_meta = build_snapshot_meta(events)
    snap_meta.to_csv(DATA / "snapshots.csv", index=False)
    print(f"  {len(events)} events, {len(snap_meta)} snapshots\n")

    for name, cfg in CONFIGS.items():
        scale_str = cfg.get("type_scales") or {"final_field": cfg.get("field_scale", 1.0)}
        print(f"=== Config: {name} (window={cfg['window']}, decay={cfg.get('decay', True)}, scales={scale_str}) ===")
        out_dir = DATA / f"ratings_{name}"
        out_dir.mkdir(exist_ok=True)
        ratings = compute_ratings(
            events, gender_map,
            window_size=cfg["window"],
            field_scale=cfg.get("field_scale", 1.0),
            recency_decay=cfg.get("decay", True),
            eos_only=cfg.get("eos_only", False),
            type_scales=cfg.get("type_scales"),
        )
        ratings = ratings.merge(snap_meta, on="ranking_id", how="left")
        ratings.to_csv(out_dir / "ratings.csv", index=False)
        print(f"  → {len(ratings)} rating rows written\n")


if __name__ == "__main__":
    main()
