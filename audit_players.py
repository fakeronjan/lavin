# =========================================================
# LAVIN — per-player audit against Fandom infobox truth.
#
# For each player in our data, fetch their Fandom page and parse the
# `InfoboxChallenger` template:
#   * challenges           — list of seasons (in italic-link format)
#   * challengewins        — number of championships
#   * eliminations         — text like "6 (5 wins, 1 loss)"
#
# Compare to our derived stats:
#   * appearances per player (seasons)
#   * championships derived from finish field
#   * elim wins/losses derived from eliminations.csv (filtered to same-gender)
#
# Output: data/audit_report.csv (and a printed summary).
# =========================================================
import re
import time
import json
from pathlib import Path

import pandas as pd
import requests
import mwparserfromhell as mwp

HERE = Path(__file__).parent
DATA = HERE / "data"
API = "https://thechallenge.fandom.com/api.php"
UA = "lavin-research/0.1 (rjsikdar@gmail.com)"
CACHE = DATA / ".fandom_player_cache"


def fetch_player_wikitext(name):
    """Cache-aware fetch of a player's Fandom page wikitext."""
    CACHE.mkdir(exist_ok=True)
    key = name.replace("/", "_").replace(" ", "_")
    cache_file = CACHE / f"{key}.txt"
    if cache_file.exists() and cache_file.stat().st_size > 0:
        return cache_file.read_text(encoding="utf-8")
    try:
        r = requests.get(
            API,
            params={"action": "parse", "page": name.replace(" ", "_"),
                    "format": "json", "prop": "wikitext", "redirects": "true"},
            headers={"User-Agent": UA},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            cache_file.write_text("", encoding="utf-8")
            return ""
        wt = j.get("parse", {}).get("wikitext", {}).get("*", "")
        cache_file.write_text(wt, encoding="utf-8")
        return wt
    except Exception as e:
        return ""


def parse_infobox(wt):
    """Return dict of InfoboxChallenger fields, or {} if not found."""
    if not wt:
        return {}
    code = mwp.parse(wt)
    for tpl in code.filter_templates():
        tname = str(tpl.name).strip().lower()
        if "infobox" in tname and "challenger" in tname:
            out = {}
            for p in tpl.params:
                k = str(p.name).strip().lower()
                v = str(p.value).strip()
                out[k] = v
            return out
    return {}


# Parse the `eliminations` field like "6 (5 wins, 1 loss)" or "4 (4 wins)" etc.
_ELIM_RE = re.compile(
    r"(\d+)\s*(?:wins?)?\s*(?:,\s*(\d+)\s*loss(?:es)?)?", re.IGNORECASE
)


def parse_elim_field(s):
    """Return (wins, losses) parsed from infobox elim string. Returns (None, None) if unparseable."""
    if not s:
        return None, None
    s = re.sub(r"<.*?>", "", s)  # strip HTML
    m = re.search(r"(\d+)\s*wins?[\s,]+(\d+)\s*loss(?:es)?", s, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Patterns like "(X wins)" only
    m = re.search(r"(\d+)\s*wins?\s*\)", s)
    if m:
        return int(m.group(1)), 0
    # Patterns like "(X losses)" only
    m = re.search(r"(\d+)\s*loss(?:es)?", s)
    if m:
        return 0, int(m.group(1))
    return None, None


def parse_challengewins(s):
    """Return integer wins from infobox challengewins field."""
    if not s:
        return None
    s = re.sub(r"<.*?>", "", s).strip()
    m = re.match(r"(\d+)", s)
    return int(m.group(1)) if m else None


def parse_challenges_seasons(s):
    """Count number of seasons listed in `challenges` field (italic-link entries)."""
    if not s:
        return 0
    # Each season is typically `'''[[Season Name|Display]]'''` separated by <br>
    # We just count the wikilinks
    return len(re.findall(r"\[\[[^\]]+\]\]", s))


# ---------------------------------------------------------
# Derive our stats from local data
# ---------------------------------------------------------
def derive_our_stats(appearances, eliminations, gender_map):
    """For each player, return dict: seasons, championships, elim_wins, elim_losses."""
    stats = {}
    # Filter eliminations to same-gender only (matches what the model actually uses)
    e = eliminations.copy()
    e["g_a"] = e["winner"].map(gender_map)
    e["g_b"] = e["loser"].map(gender_map)
    e_clean = e[(e["g_a"].isin(["M","F"])) & (e["g_a"] == e["g_b"])]

    seasons_per_player = appearances.groupby("player")["season_id"].nunique()
    for p, n in seasons_per_player.items():
        stats[p] = {"seasons": int(n), "championships": 0, "elim_wins": 0, "elim_losses": 0}

    # Championships from finish text (rank 1 = winner)
    for _, row in appearances.iterrows():
        p = row["player"]
        finish = str(row.get("finish") or "")
        if re.search(r"\bwinners?\b", finish, re.IGNORECASE):
            if p in stats:
                stats[p]["championships"] += 1

    # Elim wins/losses from same-gender elim data
    for p, n in e_clean["winner"].value_counts().items():
        if p in stats:
            stats[p]["elim_wins"] = int(n)
    for p, n in e_clean["loser"].value_counts().items():
        if p in stats:
            stats[p]["elim_losses"] = int(n)

    return stats


# ---------------------------------------------------------
# Audit
# ---------------------------------------------------------
def main():
    appearances = pd.read_csv(DATA / "appearances.csv")
    eliminations = pd.read_csv(DATA / "eliminations.csv")
    players = pd.read_csv(DATA / "players.csv")
    gender_map = dict(zip(players["player"].astype(str), players["gender"].astype(str)))

    # Only audit event-relevant players (in elims or dailies, not cast-only)
    dailies = pd.read_csv(DATA / "dailies.csv")
    event_players = set(eliminations["winner"]) | set(eliminations["loser"]) | set(dailies["winner"])
    event_players = sorted(p for p in event_players if isinstance(p, str) and p.strip())

    our_stats = derive_our_stats(appearances, eliminations, gender_map)

    print(f"Auditing {len(event_players)} event-relevant players against Fandom...")
    rows = []
    for i, name in enumerate(event_players, 1):
        wt = fetch_player_wikitext(name)
        infobox = parse_infobox(wt)
        if not infobox:
            rows.append({
                "player": name, "status": "no_infobox",
                "fandom_seasons": None, "fandom_wins": None,
                "fandom_elim_wins": None, "fandom_elim_losses": None,
                "our_seasons": our_stats.get(name, {}).get("seasons", 0),
                "our_championships": our_stats.get(name, {}).get("championships", 0),
                "our_elim_wins": our_stats.get(name, {}).get("elim_wins", 0),
                "our_elim_losses": our_stats.get(name, {}).get("elim_losses", 0),
                "diff_summary": "no Fandom infobox found",
            })
            time.sleep(0.3)
            continue

        f_seasons = parse_challenges_seasons(infobox.get("challenges", ""))
        f_wins = parse_challengewins(infobox.get("challengewins", ""))
        f_ew, f_el = parse_elim_field(infobox.get("eliminations", ""))

        us = our_stats.get(name, {})
        diffs = []
        if f_seasons and us.get("seasons", 0) and us["seasons"] != f_seasons:
            diffs.append(f"seasons {us['seasons']} vs F{f_seasons}")
        if f_wins is not None and us.get("championships", 0) != f_wins:
            diffs.append(f"wins {us['championships']} vs F{f_wins}")
        if f_ew is not None and us.get("elim_wins", 0) != f_ew:
            diffs.append(f"elimW {us.get('elim_wins',0)} vs F{f_ew}")
        if f_el is not None and us.get("elim_losses", 0) != f_el:
            diffs.append(f"elimL {us.get('elim_losses',0)} vs F{f_el}")

        rows.append({
            "player": name,
            "status": "ok" if not diffs else "mismatch",
            "fandom_seasons": f_seasons,
            "fandom_wins": f_wins,
            "fandom_elim_wins": f_ew,
            "fandom_elim_losses": f_el,
            "our_seasons": us.get("seasons", 0),
            "our_championships": us.get("championships", 0),
            "our_elim_wins": us.get("elim_wins", 0),
            "our_elim_losses": us.get("elim_losses", 0),
            "diff_summary": "; ".join(diffs) if diffs else "",
        })

        if i % 30 == 0:
            print(f"  [{i}/{len(event_players)}] processed")
        time.sleep(0.3)

    df = pd.DataFrame(rows)
    df.to_csv(DATA / "audit_report.csv", index=False)

    n_ok = (df["status"] == "ok").sum()
    n_mm = (df["status"] == "mismatch").sum()
    n_no = (df["status"] == "no_infobox").sum()
    print(f"\nAudit complete. {len(df)} players audited.")
    print(f"  OK:                 {n_ok}")
    print(f"  Mismatch w/Fandom:  {n_mm}")
    print(f"  No Fandom infobox:  {n_no}")
    print(f"\nWrote {DATA / 'audit_report.csv'}")


if __name__ == "__main__":
    main()
