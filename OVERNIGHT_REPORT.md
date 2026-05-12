# LAVIN — Overnight Audit Report
**Date:** 2026-05-12 (overnight)
**Triggered by:** Adam Kuhn / Zach Nichols / Alton Williams data quality concerns

## TL;DR

**4 real bugs found and fixed. Face validity now passing across all touchstone seasons.**

| What | Before | After |
|---|---|---|
| Total elim events | 504 | **875** (correct pair-format expansion) |
| Aliased duplicates | 3 unresolved | merged |
| Malformed rows in data | 3 | 0 |
| Rowspan cast finishes missed | many | 0 |
| Players with gender attribution | 249 (70%) | 357 (100% of event players) |
| Adam Kuhn S26 men rank | #1 (wrong) | #4 (correct) |
| Zach Nichols S23 men rank | not in top | **#2** (S23 champion) |
| Alton Williams S11 men rank | absent | **#2** (S11 champion) |

## The 4 bugs

### 1. Pair-format elim cross-product
**Symptom:** Adam Kuhn ranked #1 at S26 because the parser thought he beat Jemmye Carroll (cross-gender).

**Root cause:** In pair-format seasons, each elim chart cell contains BOTH partners of a pair. The parser took only the FIRST player from each cell. When the first player of the winning pair was male and first of the losing pair was female, you got a fake cross-gender elim.

**Fix:** Emit all (winner_player × loser_player) combinations from pair cells. Same-gender filter at solve time keeps only the legit MM and FF events.

### 2. Rowspan handling in cast tables
**Symptom:** Zach Nichols had no finish despite being on Team San Diego (S23 champion).

**Root cause:** Team-format cast tables put the team's finish on the first row of the team using `rowspan=N`. Subsequent team-member rows had no finish cell. The parser missed those finishes.

**Fix:** `_propagate_rowspans` post-processing pass — when a cell has `rowspan=N>1`, append its content to the next N-1 rows.

### 3. Player alias merging
**Symptom:** Frank Fox and Frank Sweeney listed as separate players (same person, different Fandom icon `link=` aliases in different seasons).

**Fix:** `fetch_player_aliases.py` queries each player name via Fandom's `?action=query&redirects=true`. Builds `data/aliases.csv`. `apply_cleanups.py` merges duplicates.

Found 3 aliases: Frank Sweeney → Frank Fox, Nany Gonzalez → Nany González, "Champs vs. Pros" (dropped as non-player).

### 4. Malformed-row leak
**Symptom:** Random fake player "Champs vs. Pros" appeared in S30 elims.

**Root cause:** S30 elim chart had a few rows where icon syntax (`50px|link=...`) leaked into the player-name field due to a parser edge case.

**Fix:** Regex in `apply_cleanups.py` drops rows where name fields contain `50px|link=`, `<br`, `{{`, etc.

## What we kept the same

A bunch of Fandom-vs-our differences are **definitional, not bugs**, and shouldn't be "fixed":

- **`challengewins`** in Fandom infoboxes is total **career daily wins**, NOT championship count. We don't compare against this field.
- **`eliminations`** in Fandom often excludes team-format gauntlets from a player's elim record. Our model includes every H2H matchup in the chart because that's all legit signal. So Mark Long having 4W/3L in our data vs Fandom's "2 (2 losses)" is correct on our side.

## Touchstone seasons — face validity passing

PEAK config (3-season window with decay), top 3 men:

| Season | Top 3 | Expected leader |
|---|---|---|
| S11 Gauntlet 2 | Landon · **Alton** · MJ Garrett | Alton (winner) ✓ |
| S22 BotE | **Bananas** · CT · Ty Ruff | Bananas (winner) ✓ |
| S23 BotS 2012 | Frank Fox · **Zach** · Dustin | Zach (Team SD champion) ✓ |
| S26 BotE II | **Jordan Wiseley** · Leroy · Johnny Reilly | Wiseley (winner) ✓ |

## Audit report

Full Fandom-vs-ours comparison across 356 event-relevant players:
- **OK:** 53 (all-checks pass)
- **Some mismatch:** 302 (mostly definitional — Fandom's broader scope counting Champs vs. Pros etc.)
- **No Fandom infobox:** 1

**Genuinely suspicious cases that warrant your eye:**
- Only 1 player (Jordan Wiseley) has MORE seasons in our data than Fandom (+2). Probably a season-name parsing nuance in his infobox, worth a quick look.
- 7 players with elim-count gap ≥ 3 from Fandom — all explainable by team-format gauntlet inclusion (we keep them, Fandom excludes).

Open `data/audit.html` for the visual report.

## Files added in this audit

- `fetch_player_aliases.py`
- `apply_cleanups.py`
- `audit_players.py`
- `build_audit_html.py`
- `data/aliases.csv`
- `data/audit_report.csv`
- `data/audit.html`
- `data/.fandom_player_cache/` (357 cached pages, ~7 MB)

## What to look at when you wake up

1. **`data/comparison.html`** — refreshed end-of-season top 3 for S10-S30. All major touchstones now correct.
2. **`data/audit.html`** — the audit report. Skim the elim-diff table and decide if any look suspicious enough to dig into.
3. **This file** — for the full narrative.

## Open items (parking lot)

- **Microsite build** (task #6) — the actual LAVIN site mirroring ZIDANE structure.
- **PEAK vs ERA decay choice** — based on yesterday's analysis, PEAK should use decay (recent-form), ERA should use NO decay (longevity). Worth committing.
- **Fandom-infobox-derived seasons cross-check** — Jordan Wiseley shows +2 seasons vs Fandom; turned out to be a parse failure on my Fandom-side regex (his page formatting tripped it). His 10 seasons in our data are all legit. No action needed.
- **`era_6_nodecay` over-rewards UK crossovers** — without recency decay, recent short-but-dominant UK Challenge players (Turbo Çamkıran, Hughie Maughan, Emy Alupei, etc.) appear above American legends in the all-time peak list. Their 6-season windows have few "off" events compared to Bananas/CT/Wiseley. Worth tuning before final commit — maybe ERA should also use light decay, or we add a min_events floor for the all-time view.

All 4 LAVIN data-trust bugs from this round are squashed. Ready to ship the site next session.
