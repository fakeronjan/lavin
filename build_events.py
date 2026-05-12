# =========================================================
# LAVIN — build the pairwise events.csv that feeds the WLS solver.
#
# Inputs (from build_appearances.py):
#   data/appearances.csv     — (season, player, gender, finish, team, pair_id, ...)
#   data/eliminations.csv    — (season, episode, gender, game, winner, loser)
#   data/dailies.csv         — (season, episode, challenge, format, role, winner)
#
# Output:
#   data/events.csv  — one pairwise row per event:
#     (season_id, season_episode_id, player_a, player_b, outcome, weight,
#      event_type, format)
#     outcome = 1 means player_a beat player_b; weight is sqrt-normalized.
#
# Event types & base weights:
#   elimination    1.0   pure 1v1 H2H
#   final          1.5   pairwise ranked-finish at end of season
#   daily          0.2   distributed pairwise across active opponents,
#                        further /sqrt(N_winners * N_losers) for fair
#                        attribution in pair / team format dailies.
# =========================================================
import re
import math
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"

WEIGHT_ELIM = 1.0
WEIGHT_FINAL_WITHIN = 2.0      # Pair events among ranked finalists (W>RU>3rd...)
WEIGHT_FINAL_FIELD = 1.0       # Base weight for finalist-beats-each-non-finalist;
                               # final scale applied at solve time in lavin.py
WEIGHT_DAILY_BASE = 0.2

# Anchor the model at S5 (first season with elimination signal). S2-S4
# are pure-team mission outcomes captured separately for historical reference.
MIN_SEASON_NUM = 5


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def season_num(season_id):
    """Pull the leading season number from an id like 's13_the_duel'."""
    m = re.match(r"s(\d+)_", str(season_id))
    return int(m.group(1)) if m else 9999


def episode_order(ep_str):
    """
    Convert an episode label to an integer ordering key.
    Handles '1', '5/6', '14 ', '10' etc. Falls back to a high sentinel
    for unparseable labels (so they sort after numeric episodes).
    """
    s = str(ep_str or "").strip()
    m = re.match(r"^(\d+)", s)
    if m:
        return int(m.group(1))
    return 9999


# Map placement keywords to ranks (1 is best). None = not a final placement.
# Allow plural forms — pair-format seasons use "Winners" / "Runners-Up" because
# both members of the winning pair share the placement.
FINAL_RANK_PATTERNS = [
    (re.compile(r"\bwinners?\b", re.I), 1),
    (re.compile(r"\brunners?[- ]?up\b", re.I), 2),
    (re.compile(r"\bsecond place\b", re.I), 2),
    (re.compile(r"\bthird place\b", re.I), 3),
    (re.compile(r"\bfourth place\b", re.I), 4),
    (re.compile(r"\bfifth place\b", re.I), 5),
    (re.compile(r"\bsixth place\b", re.I), 6),
    (re.compile(r"\bseventh place\b", re.I), 7),
    (re.compile(r"\beighth place\b", re.I), 8),
]


def parse_final_rank(finish_text):
    """Return integer rank (1=winner) if finish describes a final placement, else None."""
    if not isinstance(finish_text, str) or not finish_text.strip():
        return None
    for pat, rank in FINAL_RANK_PATTERNS:
        if pat.search(finish_text):
            return rank
    return None


def elim_episode_num(finish_text):
    """
    For non-final players, parse the elimination episode if encoded in the
    finish text. Mostly we use eliminations.csv directly — this is just a
    fallback for DQs and quits that don't appear in the elim chart.
    """
    return None  # not currently used; here for future extension


# ---------------------------------------------------------
# Per-season active-set computation
# ---------------------------------------------------------
def compute_active_sets(season_appearances, season_elims):
    """
    Return dict: episode_order_int -> set of players still active at start
    of that episode. A player is "active" up through (and including) the
    episode in which they were eliminated. After that they're inactive.

    Players with no elimination row are considered active throughout the
    season (finalists, DQ-on-last-day, etc.).
    """
    all_players = set(season_appearances["player"].dropna().astype(str))

    # Player -> episode they were eliminated in (lowest seen)
    elim_at = {}
    for _, row in season_elims.iterrows():
        p = str(row.get("loser") or "").strip()
        if not p:
            continue
        eo = episode_order(row.get("episode"))
        if p not in elim_at or eo < elim_at[p]:
            elim_at[p] = eo

    # Sorted unique episode-order keys we care about
    episodes = sorted(set([episode_order(e) for e in season_elims["episode"].fillna("")
                           if episode_order(e) < 9999]))
    active_at = {}
    for ep in episodes:
        active_at[ep] = {p for p in all_players if elim_at.get(p, 9999) >= ep}
    return active_at, elim_at, all_players


# ---------------------------------------------------------
# Event generators
# ---------------------------------------------------------
def build_elimination_events(elims):
    """One pairwise row per elimination: winner beats loser, weight 1.0."""
    events = []
    for _, row in elims.iterrows():
        w = str(row.get("winner") or "").strip()
        l = str(row.get("loser") or "").strip()
        if not w or not l or w == l:
            continue
        events.append({
            "season_id": row["season_id"],
            "episode": str(row.get("episode") or ""),
            "ep_ord": episode_order(row.get("episode")),
            "player_a": w,
            "player_b": l,
            "weight": WEIGHT_ELIM,
            "event_type": "elimination",
            "format": "individual",  # eliminations are always 1v1
        })
    return events


def build_final_events(appearances, gender_map):
    """
    Two kinds of finals events:

      `final_within`: pairs of ranked finishers — rank-i beats rank-j for i<j.
                      Weight WEIGHT_FINAL_WITHIN (default 2.0).
      `final_field`:  each finalist beats each non-finalist of the same
                      gender on the same season. Weight WEIGHT_FINAL_FIELD
                      base (default 1.0); the per-event scale is applied
                      at solve time in lavin.py.

    Rationale: making the finals IS implicitly beating everyone who didn't.
    A separate event type lets us tune that signal independently.
    """
    events = []
    for sid, sdf in appearances.groupby("season_id"):
        ranks = {}
        for _, row in sdf.iterrows():
            p = str(row.get("player") or "").strip()
            if not p:
                continue
            r = parse_final_rank(row.get("finish"))
            if r is not None:
                if p not in ranks or r < ranks[p]:
                    ranks[p] = r

        # Within-finals pairwise (existing logic, new event_type label)
        ranked = sorted(ranks.items(), key=lambda kv: kv[1])
        for i, (pa, ra) in enumerate(ranked):
            for j in range(i + 1, len(ranked)):
                pb, rb = ranked[j]
                if ra == rb or pa == pb:
                    continue
                events.append({
                    "season_id": sid,
                    "episode": "final",
                    "ep_ord": 9000,
                    "player_a": pa,
                    "player_b": pb,
                    "weight": WEIGHT_FINAL_WITHIN,
                    "event_type": "final_within",
                    "format": "final",
                })

        # Field-expansion: each finalist beats each non-finalist (same gender)
        finalist_set = set(ranks.keys())
        season_players = set(sdf["player"].dropna().astype(str))
        non_finalists = season_players - finalist_set
        for finalist in finalist_set:
            fg = gender_map.get(finalist)
            if fg not in ("M", "F"):
                continue
            for non_f in non_finalists:
                if gender_map.get(non_f) != fg:
                    continue
                events.append({
                    "season_id": sid,
                    "episode": "final",
                    "ep_ord": 9000,
                    "player_a": finalist,
                    "player_b": non_f,
                    "weight": WEIGHT_FINAL_FIELD,
                    "event_type": "final_field",
                    "format": "final",
                })
    return events


def build_daily_events(season_dailies, season_appearances, active_at,
                      season_id, all_players, gender_map):
    """
    Expand each daily into pairwise (winner_on_side, loser_on_side) events.

    Logic per (episode, role):
      - Winners on this side = set of players in dailies rows for this
        (episode, role). For pair/team rows we already have multiple rows.
      - Active set at this episode = compute_active_sets()[ep_ord], minus
        the winners side itself.
      - For each winner: emit pairwise vs each opposing active player of
        the same gender (eliminations are gender-segregated; dailies in
        co-ed seasons may not be, but for the rating we treat dailies as
        within-gender signal to match the eliminations they help avoid).
      - Weight per pair = WEIGHT_DAILY_BASE / sqrt(N_winners * N_losers)
        — see project_lavin_site memory for the rationale.
    """
    events = []
    if not len(season_dailies):
        return events

    grouped = season_dailies.groupby(["episode", "role"])
    for (ep, role), grp in grouped:
        eo = episode_order(ep)
        winners = sorted(set(grp["winner"].dropna().astype(str)))
        if not winners:
            continue

        # Active set at this episode (eliminated-this-episode players still
        # count as active for this daily — they were eligible).
        active = active_at.get(eo)
        if active is None:
            # No elimination rows yet (very early dailies) — use all players
            active = set(all_players)
        active = set(active)

        # Same-gender opposing pool (we run men/women separately in the model)
        for gender in ("M", "F"):
            gender_winners = [p for p in winners if gender_map.get(p) == gender]
            if not gender_winners:
                continue
            gender_active = {p for p in active if gender_map.get(p) == gender}
            losers = gender_active - set(gender_winners)
            if not losers:
                continue
            n_w, n_l = len(gender_winners), len(losers)
            w = WEIGHT_DAILY_BASE / math.sqrt(n_w * n_l)
            # Determine format label from group size
            fmt_label = "individual" if n_w == 1 else ("pair" if n_w == 2 else "team")
            for pa in gender_winners:
                for pb in losers:
                    events.append({
                        "season_id": season_id,
                        "episode": str(ep),
                        "ep_ord": eo,
                        "player_a": pa,
                        "player_b": pb,
                        "weight": w,
                        "event_type": "daily",
                        "format": fmt_label,
                    })
    return events


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
def main():
    print("Loading inputs...")
    appearances = pd.read_csv(DATA / "appearances.csv")
    eliminations = pd.read_csv(DATA / "eliminations.csv")
    dailies = pd.read_csv(DATA / "dailies.csv")
    players = pd.read_csv(DATA / "players.csv")
    gender_map = dict(zip(players["player"].astype(str), players["gender"].astype(str)))

    # Filter to model window (S5+)
    appearances["snum"] = appearances["season_id"].map(season_num)
    eliminations["snum"] = eliminations["season_id"].map(season_num)
    dailies["snum"] = dailies["season_id"].map(season_num)
    appearances = appearances[appearances["snum"] >= MIN_SEASON_NUM].copy()
    eliminations = eliminations[eliminations["snum"] >= MIN_SEASON_NUM].copy()
    dailies = dailies[dailies["snum"] >= MIN_SEASON_NUM].copy()

    print(f"  {len(appearances)} appearances, {len(eliminations)} elims, {len(dailies)} dailies (S{MIN_SEASON_NUM}+)")

    all_events = []
    print("\nBuilding elimination events...")
    elim_events = build_elimination_events(eliminations)
    print(f"  +{len(elim_events)} elim events")
    all_events.extend(elim_events)

    print("Building final-placement events...")
    final_events = build_final_events(appearances, gender_map)
    n_within = sum(1 for e in final_events if e["event_type"] == "final_within")
    n_field  = sum(1 for e in final_events if e["event_type"] == "final_field")
    print(f"  +{n_within} final_within events, +{n_field} final_field events")
    all_events.extend(final_events)

    print("Building daily events (with sqrt normalization)...")
    daily_total = 0
    for sid, sdf_app in appearances.groupby("season_id"):
        sdf_elim = eliminations[eliminations["season_id"] == sid]
        sdf_daily = dailies[dailies["season_id"] == sid]
        active_at, _, all_p = compute_active_sets(sdf_app, sdf_elim)
        evs = build_daily_events(sdf_daily, sdf_app, active_at, sid, all_p, gender_map)
        daily_total += len(evs)
        all_events.extend(evs)
    print(f"  +{daily_total} daily events")

    print(f"\nTotal events: {len(all_events)}")
    df = pd.DataFrame(all_events)
    df.to_csv(DATA / "events.csv", index=False)
    print(f"Wrote data/events.csv ({len(df)} rows)")

    # Quick stats
    print("\nBy event type:")
    print(df.groupby("event_type")["weight"].agg(["count", "sum"]).round(2))
    print("\nBy daily format:")
    print(df[df["event_type"] == "daily"].groupby("format")["weight"].agg(["count", "sum"]).round(2))


if __name__ == "__main__":
    main()
