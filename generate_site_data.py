# =========================================================
# LAVIN — generate the JSON files the docs/ microsite consumes.
#
# Outputs under docs/data/:
#   current_standings.json   — most recent end-of-season ranking
#   champions.json           — winners by season (per gender)
#   goat_players.json        — top 50 by ERA (career stature) per gender
#   goat_player_seasons.json — top 50 player-seasons by end-of-season rating
#   seasons_index.json       — list of all seasons (id, year, name, etc.)
#   seasons/<season_id>.json — end-of-season standings (per-cast) plus
#                              snapshot history
#   players_index.json       — list of all rated players
#   players/<safe_name>.json — per-player career timeline + season-by-season
# =========================================================
import json
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
DOCS = HERE / "docs"
DOCS_DATA = DOCS / "data"
DOCS_SEASONS = DOCS_DATA / "seasons"
DOCS_PLAYERS = DOCS_DATA / "players"

# Players need this many snapshots before getting a dedicated player page.
PLAYER_PAGE_MIN_SNAPSHOTS = 30


def safe_filename(name):
    """Make a filesystem-safe filename out of a player name."""
    s = re.sub(r"[^A-Za-z0-9 \-]", "", name)
    return s.strip().replace(" ", "_") or "player"


def season_label(season_id, seasons_csv):
    """'s22_battle_of_the_exes' → 'S22 Battle of the Exes (2012)'."""
    row = seasons_csv[seasons_csv["season_id"] == season_id]
    if not len(row):
        return season_id
    r = row.iloc[0]
    name = r.get("season_name") or season_id
    year = r.get("year") or ""
    num = r.get("season_num") or ""
    return f"S{int(num)} {name} ({int(year)})" if num and year else f"{name}"


def parse_rank_from_finish(finish):
    if not isinstance(finish, str):
        return None
    finish = finish.lower()
    for kw, rank in [
        ("winner", 1), ("runner", 2), ("third place", 3),
        ("fourth place", 4), ("fifth place", 5), ("sixth place", 6),
    ]:
        if kw in finish:
            return rank
    return None


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
def main():
    print("Loading inputs...")
    ratings = pd.read_csv(DATA / "ratings_lavin" / "ratings.csv")
    appearances = pd.read_csv(DATA / "appearances.csv")
    views = pd.read_csv(DATA / "player_views.csv")
    eliminations = pd.read_csv(DATA / "eliminations.csv")
    dailies = pd.read_csv(DATA / "dailies.csv")
    seasons = pd.read_csv(HERE / "seasons.csv")

    # gender map for filtering
    players_df = pd.read_csv(DATA / "players.csv")
    gmap = dict(zip(players_df["player"].astype(str), players_df["gender"].astype(str)))

    DOCS_SEASONS.mkdir(parents=True, exist_ok=True)
    DOCS_PLAYERS.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------
    # seasons_index.json
    # ---------------------------------------------------------
    s5plus = seasons[seasons["season_num"] >= 5].sort_values("season_num", ascending=False)
    season_index = []
    for _, r in s5plus.iterrows():
        sid = r["season_id"]
        cast = appearances[appearances["season_id"] == sid]
        n_cast = cast["player"].nunique()
        season_index.append({
            "season_id": sid,
            "season_num": int(r["season_num"]),
            "season_name": r["season_name"],
            "year": int(r["year"]),
            "label": season_label(sid, seasons),
            "cast_count": int(n_cast),
        })
    (DOCS_DATA / "seasons_index.json").write_text(json.dumps(season_index, indent=2))
    print(f"  Wrote seasons_index.json ({len(season_index)} seasons)")

    # ---------------------------------------------------------
    # seasons/<season_id>.json
    # ---------------------------------------------------------
    # Pre-index appearances by season for finish/rank lookup
    apps_by_season = {sid: g for sid, g in appearances.groupby("season_id")}

    for sid in s5plus["season_id"]:
        cast = apps_by_season.get(sid)
        if cast is None or not len(cast):
            continue
        cast_players = set(cast["player"].astype(str))

        # End-of-season snapshot for this season
        sub = ratings[ratings["season_id"] == sid]
        if not len(sub):
            continue
        end_rid = sub["ranking_id"].max()
        end_snap = sub[sub["ranking_id"] == end_rid]

        out = {
            "season_id": sid,
            "season_num": int(seasons[seasons["season_id"] == sid]["season_num"].iloc[0]),
            "season_name": seasons[seasons["season_id"] == sid]["season_name"].iloc[0],
            "year": int(seasons[seasons["season_id"] == sid]["year"].iloc[0]),
            "label": season_label(sid, seasons),
            "standings_at_end": {"M": [], "F": []},
        }

        for g in ("M", "F"):
            g_snap = (
                end_snap[(end_snap["gender"] == g) & (end_snap["player"].isin(cast_players))]
                .sort_values("rating", ascending=False)
            )
            rows = []
            for i, (_, row) in enumerate(g_snap.iterrows(), 1):
                p = row["player"]
                cast_row = cast[cast["player"] == p]
                finish = cast_row.iloc[0]["finish"] if len(cast_row) else ""
                # Partner / team (used as a small subtitle under player name)
                partner, team = "", ""
                if len(cast_row):
                    pid = str(cast_row.iloc[0].get("pair_id") or "").strip()
                    if pid:
                        others = [x for x in cast[cast["pair_id"] == pid]["player"] if x != p]
                        partner = others[0] if others else ""
                    team = str(cast_row.iloc[0].get("team") or "").strip()
                rows.append({
                    "rank": i,
                    "player": p,
                    "rating": round(float(row["rating"]), 3),
                    "n_events": int(row["n_events"]),
                    "finish": "" if pd.isna(finish) else str(finish),
                    "partner": partner,
                    "team": team,
                })
            out["standings_at_end"][g] = rows

        with open(DOCS_SEASONS / f"{sid}.json", "w") as f:
            json.dump(out, f, indent=2)
    print(f"  Wrote {len(list(DOCS_SEASONS.glob('*.json')))} season files")

    # ---------------------------------------------------------
    # champions.json — winners + runners-up by season + running championship totals
    # ---------------------------------------------------------
    champs = {"M": [], "F": []}
    # Walk seasons in ASCENDING order so we can accumulate per-player champ
    # counts ("3rd title", "career #2", etc.).
    s5_asc = s5plus.sort_values("season_num", ascending=True)
    cum_champs = {}  # player → running championships count (inclusive at this season)

    for _, r in s5_asc.iterrows():
        sid = r["season_id"]
        cast = apps_by_season.get(sid)
        if cast is None:
            continue
        sub = ratings[ratings["season_id"] == sid]
        end_snap = sub[sub["ranking_id"] == sub["ranking_id"].max()] if len(sub) else pd.DataFrame()

        winners = cast[cast["finish"].fillna("").str.match(r"^Winners?\b", case=False)]
        runners = cast[cast["finish"].fillna("").str.match(r"^Runners?[- ]?Up\b", case=False)]

        def make_entry(w_row, role):
            p = str(w_row["player"])
            g = gmap.get(p, "")
            if g not in ("M", "F"):
                return None
            rating_row = end_snap[end_snap["player"] == p]
            rating = float(rating_row.iloc[0]["rating"]) if len(rating_row) else None
            count_now = None
            if role == "winner":
                cum_champs[p] = cum_champs.get(p, 0) + 1
                count_now = cum_champs[p]
            return g, {
                "season_id": sid,
                "season_num": int(r["season_num"]),
                "season_name": r["season_name"],
                "year": int(r["year"]),
                "label": season_label(sid, seasons),
                "role": role,
                "player": p,
                "championship_no": count_now,  # this player's nth championship (only for winners)
                "rating_at_end": round(rating, 3) if rating is not None else None,
            }

        season_entries = {"M": {"winners": [], "runners_up": []},
                          "F": {"winners": [], "runners_up": []}}
        for _, w_row in winners.iterrows():
            r2 = make_entry(w_row, "winner")
            if r2:
                g, e = r2; season_entries[g]["winners"].append(e)
        for _, w_row in runners.iterrows():
            r2 = make_entry(w_row, "runner_up")
            if r2:
                g, e = r2; season_entries[g]["runners_up"].append(e)

        for g in ("M", "F"):
            for e in season_entries[g]["winners"]:
                champs[g].append(e)
            for e in season_entries[g]["runners_up"]:
                champs[g].append(e)

    # Sort each gender's champions by season descending for display
    champs["M"].sort(key=lambda x: (x["season_num"], 0 if x["role"] == "winner" else 1), reverse=True)
    champs["F"].sort(key=lambda x: (x["season_num"], 0 if x["role"] == "winner" else 1), reverse=True)
    # Re-sort by season descending while keeping winners before runners-up within season
    champs["M"].sort(key=lambda x: x["season_num"], reverse=True)
    champs["F"].sort(key=lambda x: x["season_num"], reverse=True)
    (DOCS_DATA / "champions.json").write_text(json.dumps(champs, indent=2))
    n_w_m = sum(1 for c in champs["M"] if c["role"] == "winner")
    n_w_f = sum(1 for c in champs["F"] if c["role"] == "winner")
    print(f"  Wrote champions.json (M winners:{n_w_m}, F winners:{n_w_f})")

    # ---------------------------------------------------------
    # goat_players.json — top 50 ERA by gender
    # ---------------------------------------------------------
    # Pre-compute championships and finals-reached counts per player.
    def _has_winner(s):
        return bool(re.match(r"^Winners?\b", str(s), re.IGNORECASE))
    def _reached_finals(s):
        s = str(s)
        return any(re.match(p, s, re.IGNORECASE) for p in [
            r"^Winners?\b", r"^Runners?[- ]?up\b",
            r"^Third\b", r"^Fourth\b", r"^Fifth\b", r"^Sixth\b",
        ])

    champ_counts = {}
    finals_counts = {}
    for _, ar in appearances.iterrows():
        p = ar["player"]
        f = ar["finish"]
        if _has_winner(f):
            champ_counts[p] = champ_counts.get(p, 0) + 1
        if _reached_finals(f):
            finals_counts[p] = finals_counts.get(p, 0) + 1

    goat = {"M": [], "F": []}
    for g in ("M", "F"):
        v_g = views[views["gender"] == g].sort_values("era_rating", ascending=False).head(50)
        for i, (_, row) in enumerate(v_g.iterrows(), 1):
            p = row["player"]
            apps_for_p = appearances[appearances["player"] == p]
            goat[g].append({
                "rank": i,
                "player": p,
                "era_rating": round(float(row["era_rating"]), 1),
                "peak_rating": round(float(row["peak_rating"]), 3),
                "active_rating": round(float(row["active_rating"]), 3),
                "n_snapshots": int(row["era_n_snapshots"]),
                "n_seasons": int(apps_for_p["season_id"].nunique()),
                "championships": int(champ_counts.get(p, 0)),
                "finals_reached": int(finals_counts.get(p, 0)),
                "peak_season": row["peak_season_id"],
            })
    (DOCS_DATA / "goat_players.json").write_text(json.dumps(goat, indent=2))
    print(f"  Wrote goat_players.json")

    # ---------------------------------------------------------
    # goat_player_seasons.json — top 50 best player-seasons by end-of-season rating
    # ---------------------------------------------------------
    # For each (player, season) compute end-of-season rating, then rank top 50 per gender
    best_seasons = []
    for sid in s5plus["season_id"]:
        cast = apps_by_season.get(sid)
        if cast is None:
            continue
        cast_players = set(cast["player"].astype(str))
        sub = ratings[ratings["season_id"] == sid]
        if not len(sub):
            continue
        end_snap = sub[sub["ranking_id"] == sub["ranking_id"].max()]
        for _, row in end_snap.iterrows():
            p = row["player"]
            if p not in cast_players:
                continue
            g = gmap.get(p, "")
            if g not in ("M", "F"):
                continue
            finish_rows = cast[cast["player"] == p]
            finish_text = finish_rows.iloc[0]["finish"] if len(finish_rows) else ""
            best_seasons.append({
                "player": p,
                "gender": g,
                "season_id": sid,
                "season_label": season_label(sid, seasons),
                "rating_at_end": round(float(row["rating"]), 3),
                "finish": "" if pd.isna(finish_text) else str(finish_text),
                "n_events": int(row["n_events"]),
            })
    goat_ps = {"M": [], "F": []}
    for g in ("M", "F"):
        ranked = sorted([x for x in best_seasons if x["gender"] == g],
                        key=lambda x: x["rating_at_end"], reverse=True)[:50]
        for i, x in enumerate(ranked, 1):
            x_out = {k: v for k, v in x.items() if k != "gender"}
            x_out["rank"] = i
            goat_ps[g].append(x_out)
    (DOCS_DATA / "goat_player_seasons.json").write_text(json.dumps(goat_ps, indent=2))
    print(f"  Wrote goat_player_seasons.json")

    # ---------------------------------------------------------
    # current_standings.json — end of S41 (most recent season)
    # ---------------------------------------------------------
    latest_sid = s5plus.iloc[0]["season_id"]
    latest_file = DOCS_SEASONS / f"{latest_sid}.json"
    if latest_file.exists():
        (DOCS_DATA / "current_standings.json").write_text(latest_file.read_text())
        print(f"  Wrote current_standings.json (mirrors {latest_sid})")

    # ---------------------------------------------------------
    # players_index.json — list of rated players (≥ min snapshots)
    # ---------------------------------------------------------
    player_index = []
    elig = views[views["era_n_snapshots"] >= PLAYER_PAGE_MIN_SNAPSHOTS]
    for _, row in elig.sort_values("era_rating", ascending=False).iterrows():
        p = row["player"]
        apps_for_p = appearances[appearances["player"] == p]
        player_index.append({
            "player": p,
            "safe_name": safe_filename(p),
            "gender": row["gender"],
            "era_rating": round(float(row["era_rating"]), 1),
            "peak_rating": round(float(row["peak_rating"]), 3),
            "active_rating": round(float(row["active_rating"]), 3),
            "n_seasons": int(apps_for_p["season_id"].nunique()),
        })
    (DOCS_DATA / "players_index.json").write_text(json.dumps(player_index, indent=2))
    print(f"  Wrote players_index.json ({len(player_index)} players)")

    # ---------------------------------------------------------
    # players/<safe_name>.json
    # ---------------------------------------------------------
    elims_by_player_w = eliminations.groupby("winner").size()
    elims_by_player_l = eliminations.groupby("loser").size()
    dailies_by_player = dailies.groupby("winner").size()

    # Pre-build a partner lookup: (season, pair_id) → list of players
    # We resolve a player's partner by finding the OTHER player with same pair_id in same season.
    pair_lookup = {}
    for _, ar in appearances.iterrows():
        pid = str(ar.get("pair_id") or "").strip()
        if not pid:
            continue
        key = (ar["season_id"], pid)
        pair_lookup.setdefault(key, []).append(str(ar["player"]))

    for _, row in elig.iterrows():
        p = row["player"]
        sf = safe_filename(p)
        apps_for_p = appearances[appearances["player"] == p].copy()
        # Per-season summary
        seasons_data = []
        for _, ar in apps_for_p.iterrows():
            sid = ar["season_id"]
            srow = seasons[seasons["season_id"] == sid]
            if not len(srow):
                continue
            srow = srow.iloc[0]
            sub = ratings[(ratings["season_id"] == sid) & (ratings["player"] == p)]
            if not len(sub):
                continue
            sub_sorted = sub.sort_values("ranking_id")
            rating_end = float(sub_sorted.iloc[-1]["rating"])
            # Partner: other player sharing this season's pair_id
            partner = ""
            pid = str(ar.get("pair_id") or "").strip()
            if pid:
                others = [x for x in pair_lookup.get((sid, pid), []) if x != p]
                partner = others[0] if others else ""
            t_raw = ar.get("team")
            team = "" if pd.isna(t_raw) else str(t_raw).strip()
            seasons_data.append({
                "season_id": sid,
                "season_num": int(srow["season_num"]),
                "season_name": srow["season_name"],
                "year": int(srow["year"]),
                "label": season_label(sid, seasons),
                "finish": "" if pd.isna(ar["finish"]) else str(ar["finish"]),
                "rating_at_end": round(rating_end, 3),
                "partner": partner,
                "team": team,
            })
        seasons_data.sort(key=lambda x: x["season_num"], reverse=True)

        # Snapshot history (rating timeline)
        timeline = ratings[ratings["player"] == p].sort_values("ranking_id")
        timeline_data = [
            {"ranking_id": int(r["ranking_id"]),
             "season_id": r["season_id"],
             "rating": round(float(r["rating"]), 3)}
            for _, r in timeline.iterrows()
        ]

        # Championships
        champs_for_p = apps_for_p[apps_for_p["finish"].fillna("").str.contains(r"^Winner", case=False, regex=True)]

        out = {
            "player": p,
            "safe_name": sf,
            "gender": row["gender"],
            "career": {
                "era_rating": round(float(row["era_rating"]), 1),
                "peak_rating": round(float(row["peak_rating"]), 3),
                "peak_season_id": row["peak_season_id"],
                "active_rating": round(float(row["active_rating"]), 3),
                "active_season_id": row["active_season_id"],
                "n_seasons": int(apps_for_p["season_id"].nunique()),
                "n_snapshots": int(row["era_n_snapshots"]),
                "championships": int(len(champs_for_p)),
                "elim_wins": int(elims_by_player_w.get(p, 0)),
                "elim_losses": int(elims_by_player_l.get(p, 0)),
                "daily_wins": int(dailies_by_player.get(p, 0)),
            },
            "seasons": seasons_data,
            "timeline": timeline_data,
        }
        with open(DOCS_PLAYERS / f"{sf}.json", "w") as f:
            json.dump(out, f, indent=2)
    print(f"  Wrote {len(list(DOCS_PLAYERS.glob('*.json')))} player files")

    print("\nAll JSON outputs ready under docs/data/")


if __name__ == "__main__":
    main()
