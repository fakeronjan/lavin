# =========================================================
# LAVIN — FANDOM SCRAPER
# Source: The Challenge Fandom wiki (https://thechallenge.fandom.com)
# Access: MediaWiki API, no auth, custom UA to bypass Cloudflare-on-HTML
# =========================================================

import requests
import mwparserfromhell as mwp
import pandas as pd
from pathlib import Path

API = "https://thechallenge.fandom.com/api.php"
UA = "lavin-research/0.1 (rjsikdar@gmail.com)"


# ---------------------------------------------------------
# Fetch
# ---------------------------------------------------------
def fetch_wikitext(page_name):
    """Fetch raw wikitext for a Fandom page. Auto-follows redirects."""
    r = requests.get(
        API,
        params={
            "action": "parse",
            "page": page_name,
            "format": "json",
            "prop": "wikitext",
            "redirects": "true",
        },
        headers={"User-Agent": UA},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"API error for {page_name}: {j['error'].get('info')}")
    return j["parse"]["wikitext"]["*"]


# ---------------------------------------------------------
# Section helpers
# ---------------------------------------------------------
def get_section(wikitext, heading):
    """Return wikitext for a single top-level section (== Heading ==), or ''."""
    code = mwp.parse(wikitext)
    for section in code.get_sections(levels=[2], include_lead=False):
        # First node of section is the heading
        headings = section.filter_headings()
        if headings and headings[0].title.strip().lower() == heading.lower():
            return str(section)
    return ""


# ---------------------------------------------------------
# Parsers (one per section)
# ---------------------------------------------------------
_MALE_CAPTIONS = {"male contestants", "males", "men", "male cast"}
_FEMALE_CAPTIONS = {"female contestants", "females", "women", "female cast"}


def _caption_gender(caption):
    """Return 'M', 'F', or None based on a table caption."""
    c = caption.strip().lower()
    if c in _MALE_CAPTIONS:
        return "M"
    if c in _FEMALE_CAPTIONS:
        return "F"
    return None


_GENERIC_CAPTIONS = {"teams", "contestants", "cast", "competitors"}


def _team_name_from_caption(caption, table_gender):
    """Return clean team name from a caption, or '' if generic / gender / empty."""
    if not caption or table_gender:
        return ""
    c = caption.strip()
    if c.lower() in _GENERIC_CAPTIONS:
        return ""
    return c


def parse_contestants(wikitext):
    """
    Return list of dicts: {player, gender, finish, origin, team, pair_id}.

    Strategy:
      - Iterate wikitables in the Contestants section.
      - Skip outer wrapper tables (no caption, contains other tables).
      - For each remaining table, capture player(s) from File-icon `link=`
        params in each row + the row's finish text.
      - Gender comes from caption when recognizable.
      - Team comes from caption when it's not a gender or generic word
        (e.g. 'Real World', 'Road Rules', 'Good Guys', 'Champions').
      - Pair_id is assigned to any row that contains 2+ player icons,
        so partners on the same row share an id (Fresh Meat, Rivals,
        Battle of the Exes, Ride or Dies, etc.).
    Pair- and team-format seasons emit multiple players per row.
    """
    sec = get_section(wikitext, "Contestants")
    if not sec:
        return []

    code = mwp.parse(sec)
    rows = []
    seen_players_this_section = set()
    row_counter = 0  # monotonic, used to build pair_id

    for table in _iter_wikitables(code):
        # Skip wrapper tables: they contain nested {| ... |} blocks
        inner_body = table[2:-2]
        if "{|" in inner_body and "|}" in inner_body:
            continue

        caption = _table_caption(table)
        table_gender = _caption_gender(caption)
        team_name = _team_name_from_caption(caption, table_gender)

        for cells in _iter_table_rows(table):
            row_counter += 1
            extracted = _extract_contestants_from_row(cells, table_gender)
            # Multi-player rows become a pair group (pair seasons).
            # Single-player rows have no pair_id.
            pair_id = f"row_{row_counter:03d}" if len(extracted) > 1 else ""
            for row in extracted:
                if row["player"] in seen_players_this_section:
                    continue
                seen_players_this_section.add(row["player"])
                row["team"] = team_name
                row["pair_id"] = pair_id
                rows.append(row)
    return rows


def _extract_contestants_from_row(cells, table_gender):
    """
    From one table row, extract one or more contestant records.
    A row may contain 1 player (individual format) or 2+ players (pair/team
    format where partners share a row). All players in the row share the
    row's finish text.
    """
    if len(cells) < 2:
        return []

    # Find all player names from File-icon link= params, dedup-preserving order
    seen = set()
    players = []
    for cell in cells:
        for name in _players_from_icons(cell):
            if name not in seen:
                seen.add(name)
                players.append(name)

    if not players:
        return []

    # Find finish text: scan cells for placement keywords
    finish = ""
    origin_pool = []
    for cell in cells:
        body = _br_to_space(_strip_cell_attrs(cell))
        c = mwp.parse(body)
        # Drop ref tags
        for tag in c.filter_tags(matches=lambda t: str(t.tag).lower() == "ref"):
            c.remove(tag)
        plain = " ".join(c.strip_code().split())
        if not plain:
            continue
        pl = plain.lower()
        if not finish and any(k in pl for k in [
            "winner", "runner", "place", "eliminated",
            "quit", "disqualif", "removed", "withdrew",
            "did not", "ejected", "champion", "finalist",
        ]):
            finish = plain
            continue
        # Origin guess: cell with wikilink mentioning Real World / Road Rules
        for wl in c.filter_wikilinks():
            wlt = str(wl.title).lower()
            if "real world" in wlt or "road rules" in wlt:
                origin_pool.append(str(wl.title).split("|")[0])
                break

    return [
        {
            "player": p,
            "gender": table_gender or "",
            "finish": finish,
            "origin": origin_pool[i] if i < len(origin_pool) else "",
        }
        for i, p in enumerate(players)
    ]


# Keep the legacy single-player extractor referenced by tests; thin wrapper
def _extract_contestant_row(cells, gender):
    rows = _extract_contestants_from_row(cells, gender)
    return rows[0] if rows else None


def _br_to_space(s):
    """Replace <br> / <br/> / <br /> with a space before code-stripping."""
    import re as _re
    return _re.sub(r"<br\s*/?>", " ", s, flags=_re.IGNORECASE)


def _iter_wikitables(code):
    """Yield wikitable strings at all nesting depths, innermost-first."""
    text = str(code)
    stack = []  # start positions
    i = 0
    while i < len(text) - 1:
        if text[i] == "{" and text[i + 1] == "|":
            stack.append(i)
            i += 2
            continue
        if text[i] == "|" and text[i + 1] == "}":
            if stack:
                start = stack.pop()
                yield text[start:i + 2]
            i += 2
            continue
        i += 1


def _table_caption(table_text):
    """
    Return the |+ caption of a wikitable. Caption appears on its own line
    BEFORE the first row separator (|-) or first data cell (| / !). Stop
    looking after that — otherwise nested tables' captions leak in.
    """
    lines = table_text.splitlines()
    # Skip the {| opening line
    for line in lines[1:]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("|+"):
            return mwp.parse(s[2:]).strip_code().strip()
        if s.startswith("|-") or s.startswith("|") or s.startswith("!"):
            break
    return ""


_ROWSPAN_RE = __import__("re").compile(r'\browspan\s*=\s*"?(\d+)"?', __import__("re").IGNORECASE)


def _cell_rowspan(cell):
    """Return the rowspan integer for a cell (default 1)."""
    if "|" not in cell:
        return 1
    head, _, _ = cell.partition("|")
    m = _ROWSPAN_RE.search(head)
    return int(m.group(1)) if m else 1


def _propagate_rowspans(rows):
    """
    Cells with `rowspan=N` (N>1) apply to the current row and the next N-1
    rows. The MediaWiki source omits those cells from subsequent rows, so
    naive row parsing makes those rows look shorter than they are.

    This post-processing pass detects rowspan cells and appends their
    content to subsequent rows so downstream extractors see the full set
    of fields. Position-faithful column reconstruction isn't required for
    our use case — we just need the finish text to be visible.
    """
    pending = []  # list of [content, remaining_rows]
    out = []
    for row in rows:
        # Expire any pending rowspan cells that have run out
        pending = [p for p in pending if p[1] > 0]
        # Append still-active rowspan content to this row
        augmented = list(row) + [p[0] for p in pending]
        out.append(augmented)
        # Decrement remaining counts on existing pending
        for p in pending:
            p[1] -= 1
        # Scan this row's cells for new rowspan=N cells and register them
        for cell in row:
            rs = _cell_rowspan(cell)
            if rs > 1:
                pending.append([cell, rs - 1])
    return out


def _iter_table_rows(table_text):
    """
    Yield each data row as a list of cell wikitext strings.
    Rows are separated by lines starting with `|-`. Cells start with `|` (not `||`-only)
    and may be multiline.
    """
    lines = table_text.splitlines()
    # Strip the wrapping `{|` and `|}`
    if lines and lines[0].startswith("{|"):
        lines = lines[1:]
    if lines and lines[-1].startswith("|}"):
        lines = lines[:-1]

    current_row = []
    current_cell = []

    def flush_cell():
        if current_cell:
            current_row.append("\n".join(current_cell).strip())
            current_cell.clear()

    def flush_row():
        flush_cell()
        if current_row:
            # Skip header-only rows (heuristic: starts with ! cells, no actual data)
            yield_row = list(current_row)
            current_row.clear()
            return yield_row
        return None

    rows = []
    for line in lines:
        s = line.rstrip()
        if s.startswith("|-"):
            r = flush_row()
            if r:
                rows.append(r)
        elif s.startswith("|+"):
            # caption — not a data cell
            continue
        elif s.startswith("|") or s.startswith("!"):
            # New cell(s). `|` for data cells, `!` for header cells.
            # We keep `!` cells too because data rows often start with one
            # (e.g. `!1/2` for an episode number in an otherwise-data row).
            flush_cell()
            sep = "||" if s.startswith("|") else "!!"
            payload = s[1:].lstrip()
            parts = _split_cells(payload, sep)
            for part in parts[:-1]:
                current_row.append(part.strip())
            current_cell.append(parts[-1])
        else:
            # Continuation of the previous cell
            if current_cell or current_row:
                current_cell.append(line)
    r = flush_row()
    if r:
        rows.append(r)
    return _propagate_rowspans(rows)


def _split_cells(payload, sep="||"):
    """Split a line on `sep` but not inside [[...]] or {{...}}."""
    parts = []
    buf = []
    depth_link = 0
    depth_tmpl = 0
    i = 0
    while i < len(payload):
        if payload[i:i+2] == "[[":
            depth_link += 1
            buf.append("[[")
            i += 2
            continue
        if payload[i:i+2] == "]]":
            depth_link = max(0, depth_link - 1)
            buf.append("]]")
            i += 2
            continue
        if payload[i:i+2] == "{{":
            depth_tmpl += 1
            buf.append("{{")
            i += 2
            continue
        if payload[i:i+2] == "}}":
            depth_tmpl = max(0, depth_tmpl - 1)
            buf.append("}}")
            i += 2
            continue
        if payload[i:i+2] == sep and depth_link == 0 and depth_tmpl == 0:
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(payload[i])
        i += 1
    parts.append("".join(buf))
    return parts


def _cell_player(cell):
    """
    Extract a player name from a cell that contains an icon wikilink like
    `[[File:WesDuelIcon.png|50px|link=Wes Bergmann]]`. mwparserfromhell
    folds the trailing `|...` segments into the wikilink's `text`, so we
    scan that for a `link=` parameter. Returns the link target if found,
    else the first non-File wikilink title, else "".
    """
    return _player_from_icon(cell) or _player_from_bare_link(cell)


def _player_from_icon(cell):
    """Extract player ONLY from a File-icon's `link=` parameter. '' if none."""
    body = _strip_cell_attrs(cell)
    code = mwp.parse(body)
    for wl in code.filter_wikilinks():
        if str(wl.title).startswith("File:"):
            text = str(wl.text) if wl.text else ""
            for piece in text.split("|"):
                piece = piece.strip()
                if piece.lower().startswith("link="):
                    name = piece.split("=", 1)[1].strip()
                    if name and not name.startswith("File:"):
                        return name
    return ""


def _players_from_icons(cell):
    """Return ALL player names from File-icon link= params in a cell (may be 0,1,many)."""
    out = []
    body = _strip_cell_attrs(cell)
    code = mwp.parse(body)
    for wl in code.filter_wikilinks():
        if str(wl.title).startswith("File:"):
            text = str(wl.text) if wl.text else ""
            for piece in text.split("|"):
                piece = piece.strip()
                if piece.lower().startswith("link="):
                    name = piece.split("=", 1)[1].strip()
                    if name and not name.startswith("File:"):
                        out.append(name)
    return out


def _player_from_bare_link(cell):
    """Fallback: first non-File wikilink in a cell."""
    body = _strip_cell_attrs(cell)
    code = mwp.parse(body)
    for wl in code.filter_wikilinks():
        title = str(wl.title)
        if not title.startswith("File:"):
            return title.split("|")[0].strip()
    return ""


_BARE_ATTRS = {
    "center", "left", "right", "nowrap", "scope", "valign",
    "rowspan", "colspan",
}


def _strip_cell_attrs(cell):
    """If a cell starts with `attr=val | ...` or `nowrap|...`, drop attr portion."""
    if "|" not in cell:
        return cell
    head, sep, tail = cell.partition("|")
    head_s = head.strip().lower()
    if "=" in head or head_s in _BARE_ATTRS:
        return tail
    return cell


def _cell_plain(cell):
    """Strip attrs + wikicode and return clean plain text for a cell.
    Also drops <ref>...</ref> footnote tags (their content is editorial, not data)."""
    body = _br_to_space(_strip_cell_attrs(cell))
    code = mwp.parse(body)
    for tag in code.filter_tags(matches=lambda t: str(t.tag).lower() == "ref"):
        code.remove(tag)
    return " ".join(code.strip_code().split())


# ---------------------------------------------------------
# Game Summary parser (individual-format seasons)
# ---------------------------------------------------------
# Strategy: don't try to map every column. The reliable invariants across
# Duel/Duel II/Rivals/Free Agents/Total Madness/Ride or Dies etc. are:
#   - First few cells: episode #, challenge name, gender
#   - Last two PLAYER cells of every data row = (elim_winner, eliminated)
#   - The cell BEFORE those last two players, if plain text (no icons),
#     is the elimination game name
#   - The first 1-2 player cells after the gender column are the daily
#     winners (prize and/or safety)
# Middle columns (tribunal, nominees, voted-in) vary by format and aren't
# needed for the rating itself — captured separately if we want them later.


def _is_player_cell(cell):
    """True if a cell contains at least one File-icon wikilink with link= param."""
    return bool(_cell_player(cell))


def parse_game_summary_individual(wikitext):
    """
    Parse the elimination chart for individual-format seasons.
    Robust to varying column counts (Duel: 10 cols, Total Madness: 13 cols, etc.)
    by anchoring on first cells (episode/challenge/gender) and last cells
    (winner/loser). Returns (eliminations, dailies).
    """
    sec = get_section(wikitext, "Game Summary")
    if not sec:
        return [], []

    elim_table = None
    for table in _iter_wikitables(mwp.parse(sec)):
        if any(kw in table for kw in [
            "Elimination chart", "Duel outcome", "Purgatory outcome",
            "elimination outcome",
        ]):
            elim_table = table
            break
    if not elim_table:
        return [], []

    eliminations = []
    dailies = []
    last_episode = ""  # carried forward for continuation rows (gender-only first cell)

    for cells in _iter_table_rows(elim_table):
        if len(cells) < 6:
            continue
        first_plain = _cell_plain(cells[0])
        if first_plain.lower() in ("episode", "#", ""):
            continue
        if "colspan" in cells[0].lower() or "elimination chart" in cells[0].lower():
            continue

        # Detect continuation row: first cell is just a gender label (Male/Female).
        # In double-elim episodes the chart often emits two rows under one
        # episode #, with the second row starting with `!Male` or `!Female`
        # instead of an episode number.
        first_lower = first_plain.lower()
        is_continuation = first_lower in ("male", "female")
        episode = last_episode if is_continuation else first_plain
        if not is_continuation:
            last_episode = episode

        # Skip rows where the elimination collapses to N/A (no real duel held,
        # e.g. due to DQ or no second nominee). Symptom: a single wide cell
        # in the elimination-outcome area containing "N/A".
        row_text = " ".join(cells)
        if "N/A" in row_text and "Eliminated" not in row_text:
            # Daily winners still valid in this row; capture them but skip elim
            elim_skipped = True
        else:
            elim_skipped = False

        player_idx = [i for i, c in enumerate(cells) if _is_player_cell(c)]
        if len(player_idx) < 2:
            continue

        if is_continuation:
            challenge = ""
            gender_text = first_lower  # the row label IS the gender
        else:
            challenge = _cell_plain(cells[1])
            gender_text = _cell_plain(cells[2]).lower() if len(cells) > 2 else ""
        elim_gender = "M" if gender_text.startswith("m") else (
            "F" if gender_text.startswith("f") else ""
        )

        if not elim_skipped:
            loser_i = player_idx[-1]
            winner_i = player_idx[-2]
            # In pair-format seasons, the winner and loser cells each contain
            # BOTH partners (an MM and an FF player). Emit ALL cross-cell
            # combinations as candidate elim rows; the same-gender filter in
            # the solver will keep only the legitimate (MM, FF) matchups.
            winner_players = _players_from_icons(cells[winner_i]) or [_cell_player(cells[winner_i])]
            loser_players  = _players_from_icons(cells[loser_i])  or [_cell_player(cells[loser_i])]
            winner_players = [p for p in winner_players if p]
            loser_players  = [p for p in loser_players  if p]
            game = ""
            if winner_i > 0 and not _is_player_cell(cells[winner_i - 1]):
                game = _cell_plain(cells[winner_i - 1])
            for w in winner_players:
                for l in loser_players:
                    if not w or not l or w == l:
                        continue
                    eliminations.append({
                        "episode": episode,
                        "gender": elim_gender,
                        "game": game,
                        "winner": w,
                        "loser": l,
                    })
            daily_range_end = winner_i
        else:
            daily_range_end = len(cells)

        # Daily winners: take the first 2 player-bearing cells after the
        # leading episode/challenge/gender cells. Each cell may contain a
        # single player (individual format), 2 players (pair format), or
        # many (team format — when the chart shows whole-team rosters).
        start_idx = 1 if is_continuation else 3
        daily_cell_idx = [i for i in player_idx if start_idx <= i < daily_range_end]
        roles = ["prize", "safety"]
        for j, idx in enumerate(daily_cell_idx[:2]):
            cell_players = _players_from_icons(cells[idx])
            if not cell_players:
                continue
            # Format reflects how many distinct competitors share this cell
            n = len(cell_players)
            fmt = "individual" if n == 1 else ("pair" if n == 2 else "team")
            role = roles[j] if j < len(roles) else "winner"
            for p in cell_players:
                dailies.append({
                    "episode": episode,
                    "challenge": challenge,
                    "format": fmt,
                    "role": role,
                    "winner": p,
                })

    return eliminations, dailies


# ---------------------------------------------------------
# Single-season runner
# ---------------------------------------------------------
def scrape_season(season_id, page_name, out_dir):
    """Scrape one season and write per-season CSVs into out_dir/<season_id>/."""
    wt = fetch_wikitext(page_name)
    season_dir = Path(out_dir) / season_id
    season_dir.mkdir(parents=True, exist_ok=True)

    # Save raw wikitext for inspection / debugging
    (season_dir / "_raw.wikitext").write_text(wt, encoding="utf-8")

    contestants = parse_contestants(wt)
    df_c = pd.DataFrame(contestants)
    df_c.insert(0, "season_id", season_id)
    df_c.to_csv(season_dir / "contestants.csv", index=False)

    eliminations, dailies = parse_game_summary_individual(wt)
    df_e = pd.DataFrame(eliminations)
    if len(df_e):
        df_e.insert(0, "season_id", season_id)
    df_e.to_csv(season_dir / "eliminations.csv", index=False)

    df_d = pd.DataFrame(dailies)
    if len(df_d):
        df_d.insert(0, "season_id", season_id)
    df_d.to_csv(season_dir / "dailies.csv", index=False)

    return {
        "season_id": season_id,
        "page_name": page_name,
        "wikitext_bytes": len(wt),
        "contestants": len(contestants),
        "eliminations": len(eliminations),
        "dailies": len(dailies),
    }


if __name__ == "__main__":
    import sys
    out = Path(__file__).parent / "data" / "raw"
    season = sys.argv[1] if len(sys.argv) > 1 else "s13_the_duel"
    page = sys.argv[2] if len(sys.argv) > 2 else "The_Duel"
    result = scrape_season(season, page, out)
    print(result)
