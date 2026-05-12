# LAVIN — The Challenge Power Rankings

Head-to-head WLS power ratings for MTV's The Challenge, covering seasons S5
(Battle of the Seasons, 2002) through S41 (Vets & New Threats, 2025).

**Live site:** https://fakeronjan.github.io/lavin/

## What this is

LAVIN turns every elimination, daily challenge, and finals placement on
The Challenge into a pairwise H2H event, then solves a weighted least-squares
(WLS) regression for player ratings. Same modeling skeleton as the rest of
the fakeronjan sports rating fleet (LOBO, DUNCAN, DILLON, ZIDANE, SALAAM,
COBI, MESSI), adapted for individual-vs-individual competition.

## Three views

- **ERA** — cumulative positive rating contribution across a career.
  Rewards quality × longevity. The all-time-greatness view.
- **PEAK** — highest rating a player ever achieved at any single snapshot.
  Captures dominant moments.
- **ACTIVE** — rating at the player's most recent snapshot. Reflects current form.

## Pipeline

```
scrape_fandom.py        — fetch each season's wikitext from Fandom API
scrape_all.py           — iterate seasons.csv (S2-S41)
fetch_player_genders.py — Fandom-category gender for team-era players
fetch_player_aliases.py — canonical name resolution via redirects
apply_cleanups.py       — apply aliases + drop malformed rows
build_appearances.py    — consolidate per-season → appearances.csv
build_events.py         — pairwise events with sqrt-normalized weights
lavin.py                — WLS solver (single config: 6-season window, no decay)
derive_views.py         — PEAK / ERA / ACTIVE per player
generate_site_data.py   — emit docs/data/*.json for the microsite
audit_players.py        — Fandom infobox truth check (run periodically)
```

## Data source

The Challenge Fandom wiki (`thechallenge.fandom.com`) via its MediaWiki API.
Cast / Game Summary parsing handles individual, pair, and team format
elimination charts. Player gender attribution combines cast-table captions
where available with Fandom page categories (`Male_Contestants` /
`Female_Contestants`) as a fallback for team-format-only players.

## Modeling notes

- **Event weights:** elimination = 1.0, finals_within = 2.0,
  finals_field = 0.10 × base, dailies = sqrt-normalized base 0.2.
- **Ranking cadence:** one snapshot per elimination; dailies between two
  eliminations fold into the next elim's snapshot. Era-consistent across
  TV episode structure changes.
- **Anchor:** S5 (first season with individual elimination signal).
  S2-S4 are pure-team mission outcomes — present in champions for
  historical context but don't feed the rating.

## License

Personal project. See https://github.com/fakeronjan for related sites.
