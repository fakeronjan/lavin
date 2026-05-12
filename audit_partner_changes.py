# =========================================================
# LAVIN — audit mid-season partner swaps.
#
# Walk each pair-format season's elim chart in episode order. Every pair-
# cell (a cast-icon cell containing exactly 2 player icons) is a pair
# observation. If a player is seen with multiple distinct partners
# across episodes, there's a mid-season swap.
#
# Output: data/partner_changes.csv with rows
#   (season_id, player, partner_order, partner, first_episode, last_episode)
# plus a summary printed to stdout.
# =========================================================
import re
from pathlib import Path

import pandas as pd
import mwparserfromhell as mwp

HERE = Path(__file__).parent
DATA = HERE / "data"
RAW = DATA / "raw"

OUT = DATA / "partner_changes.csv"


def parse_int_episode(s):
    """Parse leading integer from an episode label ('1', '5/6', '8/9') → int."""
    s = str(s or "").strip()
    m = re.match(r"^(\d+)", s)
    return int(m.group(1)) if m else None


def extract_pairs_from_game_summary(wt):
    """
    Yield (episode_int, [player1, player2]) for every 2-icon pair-cell in
    the Game Summary section. Walks the chart row by row, tracking the
    current episode number from the leftmost `!N` cell.
    """
    sec_m = re.search(r"==\s*Game Summary\s*==(.*?)(?:^==[^=]|\Z)", wt, re.DOTALL | re.MULTILINE)
    if not sec_m:
        return
    section = sec_m.group(1)

    current_episode = None
    # Walk lines so we can track row boundaries (|-) and the episode marker
    for line in section.splitlines():
        s = line.rstrip()
        if s.startswith("|-"):
            continue
        # New episode header like `! 4` or `!1/2`
        m = re.match(r"^!\s*(\d+(?:/\d+)?)", s)
        if m:
            current_episode = parse_int_episode(m.group(1))
            continue
        # Look for cells with 2 player icons
        # Each File icon: [[File:...png|...|link=Player Name]]
        icons = re.findall(r"\[\[File:[^\]]*link=([^\]|]+)\]\]", s)
        if len(icons) == 2:
            yield current_episode, [icons[0].strip(), icons[1].strip()]


def main():
    seasons = pd.read_csv(HERE / "seasons.csv")
    rows = []
    summary_rows = []

    for _, srow in seasons.iterrows():
        sid = srow["season_id"]
        raw_path = RAW / sid / "_raw.wikitext"
        if not raw_path.exists():
            continue
        wt = raw_path.read_text(encoding="utf-8")

        # Collect pair observations per player
        player_partners = {}  # player -> {partner: [episode, ...]}
        for episode, pair in extract_pairs_from_game_summary(wt):
            if not pair or len(pair) != 2:
                continue
            a, b = pair
            for me, them in [(a, b), (b, a)]:
                player_partners.setdefault(me, {}).setdefault(them, []).append(episode or 99)

        # Find players with multiple partners
        for player, partners in player_partners.items():
            if len(partners) < 2:
                continue
            # Order partners by first-seen episode
            ordered = sorted(partners.items(), key=lambda kv: min(kv[1] or [99]))
            for i, (p, eps) in enumerate(ordered, 1):
                rows.append({
                    "season_id": sid,
                    "player": player,
                    "partner_order": i,  # 1 = original, 2+ = replacement(s)
                    "partner": p,
                    "first_episode": min(eps) if eps else None,
                    "last_episode": max(eps) if eps else None,
                })
            summary_rows.append({
                "season_id": sid,
                "player": player,
                "n_partners": len(partners),
                "partners": " → ".join(p for p, _ in ordered),
            })

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)

    sdf = pd.DataFrame(summary_rows).sort_values(["season_id", "player"])
    print(f"Wrote {OUT} ({len(df)} pair observations)")
    print()
    print(f"=== Players with multiple partners (mid-season swaps) — {len(sdf)} cases ===")
    print(sdf.to_string(index=False))


if __name__ == "__main__":
    main()
