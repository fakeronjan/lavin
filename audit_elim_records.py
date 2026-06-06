# =========================================================
# LAVIN - audit per-elim H2H records vs Fandom personal pages.
#
# For each player with a cached Fandom page, parse the per-season
# "===Elimination History===" tables. Headers vary by season:
#   4-col: Episode | Elimination | Opponent | Result
#   5-col: Episode | Elimination | Partner | Opponent(s) | Result
#   pair w/ rowspan: game column has rowspan=N for multi-round elims
#
# Strategy: read header row, identify the "Opponent" column by name,
# then use that index for each data row. Handle rowspan-merged cells
# (game cell shared across consecutive rows of the same elim).
#
# Output: diff report - what's in Fandom but not in our eliminations.csv,
# and what's in our data but not in Fandom.
# =========================================================
import re
from pathlib import Path
import pandas as pd
import mwparserfromhell as mwp

HERE = Path(__file__).parent
DATA = HERE / "data"
CACHE = DATA / ".fandom_player_cache"


def build_season_map():
    """
    Build {h2_title_lower → season_id}. Player-page h2 titles vary:
      - "The Challenge: Vets & New Threats" (verbatim page_name)
      - "Vets & New Threats" (page_name minus prefix)
      - "Vets and New Threats" (season_name with "and")
      - "Battle of the Seasons (2012)" (parenthetical year)
    """
    seasons = pd.read_csv(HERE / "seasons.csv")
    m = {}
    seen_keys = {}  # key -> sid, to detect ambiguous keys (BotS 2002 vs 2012)
    def add(k, sid):
        k = k.lower().strip()
        if k in seen_keys:
            # Ambiguous - keep the FIRST season's claim. Iteration order is
            # chronological (seasons.csv is sorted), so the unstripped key
            # ("Battle of the Seasons") goes to s05 BotS, while the
            # parenthetical year variant ("Battle of the Seasons (2012)")
            # only gets added once (by s23) and goes to s23. This recovers
            # S5 attribution without re-introducing the S23 over-count.
            return
        seen_keys[k] = sid
        m[k] = sid
    for _, r in seasons.iterrows():
        sid = r["season_id"]
        pn, sn = r["page_name"], r["season_name"]
        variants = set()
        for key in (pn, sn, pn.replace("_", " ")):
            variants.add(key)
            # Strip generic prefix (anything up to and including first colon)
            if ":" in key:
                variants.add(key.split(":", 1)[1].strip())
            # Strip parenthetical
            variants.add(re.sub(r"\s*\(.*?\)", "", key).strip())
        # Apply &/and swaps to all variants
        all_v = set(variants)
        for v in variants:
            all_v.add(v.replace(" & ", " and "))
            all_v.add(v.replace(" and ", " & "))
        for v in all_v:
            add(v, sid)
    return m


_OPP_HEADER_RE = re.compile(r"\bopponents?\b", re.IGNORECASE)
_RESULT_HEADER_RE = re.compile(r"\bresults?\b|\boutcomes?\b", re.IGNORECASE)


def _players_from_cell(cell_wt):
    if not cell_wt:
        return []
    out = []
    for m in re.finditer(
        r"\[\[(?:File|Image):[^|\]]+\|[^\]]*?link=([^|\]]+)[^\]]*\]\]", cell_wt
    ):
        out.append(m.group(1).strip())
    if not out:
        for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", cell_wt):
            t = m.group(1).strip()
            if not t.lower().startswith(("file:", "image:")):
                out.append(t)
    seen = set()
    uniq = []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def _strip_attrs(c):
    if "|" not in c:
        return c
    head, _, tail = c.partition("|")
    hl = head.strip().lower()
    if "=" in head or hl in ("align", "nowrap", "center", "rowspan", "colspan"):
        return tail
    return c


def _plain(c):
    return " ".join(mwp.parse(c).strip_code().split())


def _split_table_rows(table_text):
    """
    Split a wiki table into (header_cells, [data_rows]).

    Two wiki conventions for header placement:
      A) Headers immediately after `{|`:   `{|\n!H1\n!H2\n|-\n|D1...`
      B) Headers after first `|-`:          `{|\n|-\n!H1\n!H2\n|-\n|D1...`

    We detect both by looking at each `|-`-delimited chunk in order and
    treating the first chunk that contains `!`-prefixed cells as the
    header. Subsequent chunks (with `|`-prefixed cells) are data rows.
    """
    chunks = re.split(r"\n\|-", table_text)
    header_cells = []
    data_rows = []
    header_found = False
    for chunk in chunks:
        # Strip the leading `{|...` table-opening line (before any \n|- it
        # appears as part of chunk 0; we don't want it as a data cell).
        chunk_no_open = re.sub(r"^\s*\{\|[^\n]*\n", "", chunk)

        # Try to extract header cells from this chunk (lines starting with !)
        h_cells = []
        for line in chunk_no_open.split("\n"):
            s = line.strip()
            if s.startswith("!"):
                for h in s.lstrip("!").split("!!"):
                    h_cells.append(h.strip())
        if not header_found and h_cells:
            header_cells = h_cells
            header_found = True
            continue
        # Data row: split cell-by-cell on `\n|` or `\n!`. Use chunk_no_open
        # so the {| opener doesn't get parsed as a cell.
        # Also need to handle the case where the chunk starts directly with
        # a cell (no leading \n|), so prepend \n if needed.
        body = chunk_no_open
        if not body.startswith("\n"):
            body = "\n" + body
        cells = [c.strip() for c in re.split(r"\n[!|]", body) if c.strip()]
        if cells:
            data_rows.append(cells)
    return header_cells, data_rows


def _row_cell_count_with_rowspan(cell_text):
    """Return rowspan integer (default 1) for a cell."""
    m = re.search(r'\browspan\s*=\s*"?(\d+)"?', cell_text, re.IGNORECASE)
    return int(m.group(1)) if m else 1


def _cell_colspan(cell_text):
    """Return colspan integer (default 1) for a cell."""
    m = re.search(r'\bcolspan\s*=\s*"?(\d+)"?', cell_text, re.IGNORECASE)
    return int(m.group(1)) if m else 1


def _header_columns(header_cells):
    """
    Given header cells, return [(header_text, start_col, span)] expanded across
    the data-column grid. e.g. headers ['Episode', 'Elimination',
    'colspan=2|Opponents', 'Result'] yields:
      [('Episode', 0, 1), ('Elimination', 1, 1),
       ('Opponents', 2, 2), ('Result', 4, 1)]
    Data rows then map column N (0-indexed) to whichever header_text covers it.
    """
    cols = []
    pos = 0
    for h in header_cells:
        cs = _cell_colspan(h)
        text = _plain(_strip_attrs(h))
        cols.append((text, pos, cs))
        pos += cs
    return cols


def parse_player_elims(wt, player_name, season_map):
    """Yield (season_id, episode, game, opponents, result) for each row."""
    if not wt:
        return
    parts = re.split(r"\n==([^=][^=]*?)==\n", wt)
    for i in range(1, len(parts), 2):
        season_title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        em = re.search(
            r"===\s*Elimination History\s*===\s*\n(\{\|.*?\n\|\})", body, re.DOTALL
        )
        if not em:
            continue
        table_text = em.group(1)

        st = season_title.lower().strip()
        sid = season_map.get(st)
        if not sid:
            st2 = re.sub(r"\(.*?\)", "", st).strip()
            sid = season_map.get(st2)
        if not sid:
            continue

        header, data_rows = _split_table_rows(table_text)
        if not header or not data_rows:
            continue
        # Expand headers with colspan so we can map data columns to headers.
        # S16/S29/S33/S35/S37/S38/S39 use `colspan=2|Opponents` for two
        # opponent cells, which the old indexing logic flattened to one cell.
        header_cols = _header_columns(header)
        opp_start = opp_span = None
        res_start = None
        for text, start, span in header_cols:
            if opp_start is None and _OPP_HEADER_RE.search(text):
                opp_start, opp_span = start, span
            if res_start is None and _RESULT_HEADER_RE.search(text):
                res_start = start
        if opp_start is None or res_start is None:
            continue
        opp_idx = opp_start
        res_idx = res_start

        # Walk data rows, handling rowspan AND colspan: track which columns
        # are "filled in" by a prior rowspan and expand cells with colspan>1
        # across multiple grid columns. The opponent column may span multiple
        # data cells (S16/S29/S33/S35/S37/S38/S39 use `colspan=2|Opponent(s)`),
        # so we collect every cell whose grid position falls in the opponent
        # range, not just the one at opp_start.
        pending = {}  # col_idx -> (cell_text, remaining_rows)
        for row in data_rows:
            # Reconstruct full row by inserting pending rowspan cells AND
            # expanding colspan cells. full_row[col] points to the source
            # cell text covering that grid column.
            full_row = []
            row_cells = list(row)
            target_len = max(opp_idx + (opp_span or 1), res_idx + 1)
            col = 0
            while col < target_len:
                if col in pending:
                    full_row.append(pending[col][0])
                    pending[col] = (pending[col][0], pending[col][1] - 1)
                    if pending[col][1] <= 0:
                        del pending[col]
                    col += 1
                else:
                    if not row_cells:
                        full_row.append("")
                        col += 1
                        continue
                    cell = row_cells.pop(0)
                    cs = _cell_colspan(cell)
                    rs = _row_cell_count_with_rowspan(cell)
                    for k in range(cs):
                        full_row.append(cell)
                        if rs > 1:
                            pending[col + k] = (cell, rs - 1)
                    col += cs
            # Append any remaining cells beyond target_len so we don't lose them
            full_row.extend(row_cells)

            if len(full_row) <= max(opp_idx, res_idx):
                continue
            ep_cell = _strip_attrs(full_row[0])
            game_cell = _strip_attrs(full_row[1]) if len(full_row) > 1 else ""
            # Collect every data cell covering the opponent column range.
            opp_cells = []
            seen_ids = set()
            for k in range(opp_idx, opp_idx + (opp_span or 1)):
                if k < len(full_row):
                    cid = id(full_row[k])
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        opp_cells.append(full_row[k])
            res_cell = _strip_attrs(full_row[res_idx])

            opponents = []
            opp_seen = set()
            for oc in opp_cells:
                for p in _players_from_cell(_strip_attrs(oc)):
                    if p not in opp_seen:
                        opp_seen.add(p)
                        opponents.append(p)
            res_players = _players_from_cell(res_cell)
            res_text = _plain(res_cell).upper()

            if "WIN" in res_text:
                result = "W"
            elif "OUT" in res_text or "LOSS" in res_text:
                result = "L"
            elif player_name in res_players:
                result = "W"
            elif res_players:
                result = "L"
            else:
                low = res_cell.lower()
                if any(c in low for c in ("tomato", "firebrick", "salmon", "lightcoral")):
                    result = "L"
                elif any(c in low for c in ("navy", "blue", "greenyellow", "deepskyblue", "lightgreen", "mediumblue")):
                    result = "W"
                else:
                    result = "?"

            yield {
                "season_id": sid,
                "episode": _plain(ep_cell),
                "game": _plain(game_cell),
                "opponents": opponents,
                "result": result,
            }


def build_fandom_truth():
    season_map = build_season_map()
    truth = []
    for cf in sorted(CACHE.iterdir()):
        if not cf.is_file() or cf.stat().st_size == 0:
            continue
        wt = cf.read_text(encoding="utf-8")
        player = cf.stem.replace("_", " ")
        for rec in parse_player_elims(wt, player, season_map):
            for opp in rec["opponents"]:
                if opp == player:  # skip self-references
                    continue
                truth.append({
                    "season_id": rec["season_id"],
                    "player": player,
                    "episode": rec["episode"],
                    "game": rec["game"],
                    "opponent": opp,
                    "fandom_result": rec["result"],
                })
    return pd.DataFrame(truth)


def diff_vs_ours(truth):
    """
    Compare same-gender H2H entries only. Mixed-gender cross-product elims
    we emit for pair-format seasons aren't typically tracked on Fandom
    personal pages (Fandom lists opponents you faced in your gender's
    Arena, partner-pair attribution is implicit), so they'd skew the diff.
    """
    e = pd.read_csv(DATA / "eliminations.csv")
    players = pd.read_csv(DATA / "players.csv")
    gmap = dict(zip(players["player"], players["gender"]))

    # Mercenary cameos: Fandom records the matchup ONLY on the contestant's
    # personal page (e.g., Kyland Young's page shows "W vs Brad Fiorenza"
    # but Brad's page doesn't). Filter the merc-side ordered pair from our
    # set, but keep the contestant-side so the contestant's record still
    # matches Fandom truth.
    appearances = pd.read_csv(DATA / "appearances.csv")
    merc_pairs = set(zip(
        appearances.loc[appearances["finish"] == "Champion Mercenary", "season_id"].astype(str),
        appearances.loc[appearances["finish"] == "Champion Mercenary", "player"].astype(str),
    ))

    # Vote / face-off seasons (S5, S6, S9, S16) have no 1v1 elimination games -
    # our scrape pairs the daily/face-off winner with the voted-out player,
    # fabricating H2H matchups Fandom never records. build_events.py already
    # skips these from rating events; exclude them here too so the audit diff
    # isn't dominated by matchups we've established aren't real. Detected
    # structurally: a season where no elim row carries a named game.
    season_has_game = e.assign(_g=e["game"].notna()).groupby("season_id")["_g"].any()
    gameless_seasons = set(season_has_game[~season_has_game].index)

    ours_set = set()
    for _, r in e.iterrows():
        w, l, sid = r.get("winner"), r.get("loser"), r["season_id"]
        if not (isinstance(w, str) and isinstance(l, str)):
            continue
        if sid in gameless_seasons:  # vote/face-off season - not real H2H
            continue
        if gmap.get(w) != gmap.get(l):  # filter mixed-gender cross-product
            continue
        if (sid, w) not in merc_pairs:
            ours_set.add((sid, w, l, "W"))
        if (sid, l) not in merc_pairs:
            ours_set.add((sid, l, w, "L"))

    truth_set = set()
    for _, r in truth.iterrows():
        if r["fandom_result"] not in ("W", "L"):
            continue
        if r["season_id"] in gameless_seasons:  # vote/face-off - excluded both sides
            continue
        if gmap.get(r["player"]) != gmap.get(r["opponent"]):
            continue
        truth_set.add((r["season_id"], r["player"], r["opponent"], r["fandom_result"]))

    return truth_set - ours_set, ours_set - truth_set, truth_set, ours_set


def infer_unparsed_results(truth):
    """Some Fandom result cells don't parse to W/L (odd color/markup) and come
    back as "?". When the opponent's reciprocal row IS parsed, infer this row:
    if B shows "W vs A", then A's result vs B is "L" (and vice versa). Recovers
    ~38 rows that would otherwise show as false extras/missings."""
    known = {(r.season_id, r.player, r.opponent): r.fandom_result
             for r in truth.itertuples() if r.fandom_result in ("W", "L")}
    flip = {"W": "L", "L": "W"}
    fixed = 0
    res = truth["fandom_result"].tolist()
    for i, r in enumerate(truth.itertuples()):
        if r.fandom_result == "?":
            opp = known.get((r.season_id, r.opponent, r.player))
            if opp:
                res[i] = flip[opp]
                fixed += 1
    truth = truth.copy()
    truth["fandom_result"] = res
    print(f"  inferred {fixed} unparsed '?' results from reciprocal opponent rows")
    return truth


def main():
    print("Parsing Fandom personal pages...")
    truth = build_fandom_truth()
    print(f"  {len(truth)} per-opponent rows from {truth['player'].nunique()} players "
          f"(covering {truth['season_id'].nunique()} seasons)")
    truth = infer_unparsed_results(truth)

    missing, extra, truth_set, ours_set = diff_vs_ours(truth)

    # Filter to player pairs where BOTH have a cached Fandom page
    cached = {cf.stem.replace("_", " ") for cf in CACHE.iterdir() if cf.is_file()}
    missing_filt = [m for m in missing if m[1] in cached and m[2] in cached]
    extra_filt = [m for m in extra if m[1] in cached and m[2] in cached]

    print(f"\n=== Diff (filtered to player pairs with both Fandom pages cached) ===")
    print(f"  Missing in ours (Fandom has, we don't): {len(missing_filt)}")
    print(f"  Extra in ours   (we have, Fandom doesn't): {len(extra_filt)}")

    from collections import Counter
    print("\n  Missing by season:")
    for sid, n in sorted(Counter(m[0] for m in missing_filt).items()):
        print(f"    {sid}: {n}")
    print("\n  Extra by season:")
    for sid, n in sorted(Counter(m[0] for m in extra_filt).items()):
        print(f"    {sid}: {n}")

    pd.DataFrame(missing_filt, columns=["season_id", "player", "opponent", "fandom_result"])\
      .sort_values(["season_id", "player"]).to_csv(DATA / "audit_elim_missing.csv", index=False)
    pd.DataFrame(extra_filt, columns=["season_id", "player", "opponent", "our_result"])\
      .sort_values(["season_id", "player"]).to_csv(DATA / "audit_elim_extra.csv", index=False)
    truth.to_csv(DATA / "audit_elim_fandom_truth.csv", index=False)

    print(f"\nWrote audit CSVs to data/audit_elim_*.csv")


if __name__ == "__main__":
    main()
