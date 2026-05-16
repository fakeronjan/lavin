# =========================================================
# LAVIN — FANDOM SCRAPER
# Source: The Challenge Fandom wiki (https://thechallenge.fandom.com)
# Access: MediaWiki API, no auth, custom UA to bypass Cloudflare-on-HTML
# =========================================================

import re

import requests
import mwparserfromhell as mwp
import pandas as pd
from pathlib import Path

API = "https://thechallenge.fandom.com/api.php"
UA = "lavin-research/0.1 (rjsikdar@gmail.com)"


# Manual season-level team rosters. Use this when the cast page doesn't
# expose team labels in a way the contestants parser can pick up, but the
# elimination chart's daily-winner column uses team names that we need to
# expand into rosters (BotS-style "Power Team" charts). Manual entries
# take precedence over auto-detected ones; missing seasons fall back to
# whatever the cast parser found in the `team` column.
MANUAL_TEAM_ROSTERS = {
    "s05_battle_of_the_seasons": {
        "Real World": [
            "Sean Duffy", "Elka Walker", "Mike Mizanin", "Coral Smith",
            "Danny Roberts", "Kelley Limp", "Norman Korpi", "Becky Blasband",
            "Mike Lambert", "Flora Alekseyeva", "Stephen Williams",
            "Lindsay Brien", "Mike Johnson", "Sharon Gitau", "Jon Brennan",
            "Beth Stolarczyk",
        ],
        "Road Rules": [
            "Theo von Kurnatowski", "Holly Brentson", "Dan Setzler",
            "Tara McDaniel", "Timmy Beggy", "Emily Bailey", "Josh Florence",
            "Holly Shand", "Adam Larson", "Jisela Delgado", "Chris Melling",
            "Belou Den Tex", "Chadwick Pelletier", "Piggy Thomas", "Yes Duffy",
            "Veronica Portillo",
        ],
    },
    "s23_battle_of_the_seasons_2012": {
        "Team Austin": ["Danny Jamieson", "Melinda Collins", "Lacey Buehler", "Wes Bergmann"],
        "Team Brooklyn": ["Chet Cannon", "Devyn Simone", "JD Ordoñez", "Sarah Rice"],
        "Team Cancun": ["Derek Chavez", "Jonna Mannion", "CJ Koegel", "Jasmine Reynaud"],
        "Team Fresh Meat": ["Camila Nakagawa", "Big Easy Banks", "Brandon Nelson", "Cara Maria Sorbello"],
        "Team Las Vegas": ["Dustin Zito", "Trishelle Cannatella", "Alton Williams", "Nany González"],
        "Team New Orleans": ["Jemmye Carroll", "Ryan Knight", "McKenzie Coburn", "Preston Roberson-Charles"],
        "Team San Diego": ["Ashley Kelsey", "Frank Fox", "Sam McGinn", "Zach Nichols"],
        "Team St. Thomas": ["Marie Roda", "Robb Schreiber", "Laura Waller", "Trey Weatherholtz"],
    },
    "s40_battle_of_the_eras": {
        "Era I": [
            "Rachel Robinson", "Derrick Kosinski", "Darrell Taylor", "Tina Barta",
            "Brad Fiorenza", "CT Tamburello", "Aneesa Ferreira", "Jodi Weatherton",
            "Katie Cooley", "Mark Long",
        ],
        "Era II": [
            "Derek Chavez", "Johnny Bananas", "Cara Maria Sorbello", "Aviv Melmed",
            "Nehemiah Clark", "Laurel Stucky", "Ryan Kehoe", "Emily Schromm",
            "Brandon Nelson", "KellyAnne Judd",
        ],
        "Era III": [
            "Jordan Wiseley", "Tori Deal", "Cory Wharton", "Nia Moore",
            "Devin Walker", "Averey Tressler", "Jonna Mannion", "Tony Raines",
            "Leroy Garrett", "Amanda Garcia",
        ],
        "Era IV": [
            "Jenny West", "Michele Fitzgerald", "Kyland Young", "Josh Martinez",
            "Olivia Kaiser", "Theo Campbell", "Kaycee Clark", "Horacio Gutiérrez Jr.",
            "Nurys Mateo", "Paulie Calafiore",
        ],
    },
}


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


def _cell_colspan(cell):
    """Extract colspan=N from a cell's attributes (default 1)."""
    m = re.search(r'\bcolspan\s*=\s*"?(\d+)"?', cell, re.IGNORECASE)
    return int(m.group(1)) if m else 1


def _detect_pair_partner_columns(table):
    """
    Detect "Male partner | Female partner | Finish" column structure.
    Returns (m_end_col, f_end_col) — logical columns < m_end are M, columns
    < f_end are F, anything >= is Finish/other. Returns None if not such a
    table.
    """
    for cells in _iter_table_rows(table):
        # Header row: every cell starts with '!' (header marker) or contains
        # only header-like text. We detect by content.
        header_text = " | ".join(_cell_plain(c).lower() for c in cells)
        if "male" not in header_text or "female" not in header_text:
            continue
        if not (("male partner" in header_text) or ("males" in header_text and "females" in header_text)):
            continue
        # Walk cells, tracking logical column position via colspan.
        col = 0
        m_end = None
        f_end = None
        for c in cells:
            plain = _cell_plain(c).strip().lower()
            span = _cell_colspan(c)
            if "male" in plain and "female" not in plain and m_end is None:
                m_end = col + span
            elif "female" in plain and f_end is None:
                f_end = col + span
            col += span
        if m_end is not None and f_end is not None and m_end < f_end:
            return (m_end, f_end)
    return None


def _team_name_from_caption(caption, table_gender):
    """Return clean team name from a caption, or '' if generic / gender / empty."""
    if not caption or table_gender:
        return ""
    c = caption.strip()
    if c.lower() in _GENERIC_CAPTIONS:
        return ""
    return c


_EXIT_RE = re.compile(
    r'\b(QUIT|MED|EJECTED|REMOVED|WITHDREW|WD|DISQUALIFIED|DQ)\b',
    re.IGNORECASE,
)


def parse_episode_progress(wikitext, cast_players=None):
    """
    Parse the `===Episode Progress===` table to find players who exited the
    season early (QUIT / MED-DQ / DQ / withdrew). Used to filter team-roster
    daily expansions so e.g. S20 Shauvon (quit ep 3) doesn't get credit for
    Grey Team's ep 4+ wins.

    Episode Progress rows typically use display names ("Coral", "Shauvon"),
    not the canonical [[wikilink]] names ("Coral Smith", "Shauvon Torres").
    When `cast_players` is provided, we canonicalize via first-name match.

    Returns: dict {canonical_player_name: exit_episode_number}.
    """
    m = re.search(r'==={1,}\s*Episode Progress\s*={1,}=={0,}\s*\n', wikitext)
    if not m:
        return {}
    body_start = m.end()
    end = wikitext.find('\n==', body_start)
    body = wikitext[body_start:end if end > 0 else len(wikitext)]
    tables = list(_iter_wikitables(mwp.parse(body)))
    if not tables:
        return {}
    rows = _iter_table_rows(tables[0])
    if len(rows) < 2:
        return {}

    # Header row 2 has episode labels; first row is usually "Contestants /
    # Episodes". Pick the first row that doesn't say "Contestant".
    header_ep_row = None
    for r in rows[:3]:
        sample = ' '.join(_cell_plain(c) for c in r[:5]).lower()
        if 'contestant' in sample:
            continue
        header_ep_row = r
        break
    if header_ep_row is None:
        return {}

    # Map each sub-column index → episode number (handles colspan>1 episodes)
    cell_to_ep = []
    for cell in header_ep_row:
        cs = _cell_colspan(cell)
        text = _cell_plain(_strip_cell_attrs(cell)).strip()
        em = re.match(r'(\d+)', text)
        ep = int(em.group(1)) if em else None
        for _ in range(cs):
            cell_to_ep.append(ep)

    # Canonical-name lookup: first-name → canonical (only used when unique)
    fn_map = {}
    if cast_players:
        for p in cast_players:
            first = p.split()[0]
            fn_map.setdefault(first, []).append(p)

    exits = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        display = _cell_plain(_strip_cell_attrs(row[0])).strip()
        if not display:
            continue
        # Walk cells (colspan-aware) and find FIRST exit status
        col = 0
        exit_ep = None
        for cell in row[1:]:
            cs = _cell_colspan(cell)
            text = _cell_plain(_strip_cell_attrs(cell)).strip().upper()
            if _EXIT_RE.search(text):
                exit_ep = cell_to_ep[col] if col < len(cell_to_ep) else None
                break
            col += cs
        if exit_ep is None:
            continue
        # Canonicalize via cast first-name match (unique only)
        canonical = display
        if fn_map:
            matches = fn_map.get(display.split()[0]) or fn_map.get(display)
            if matches and len(matches) == 1:
                canonical = matches[0]
        exits[canonical] = exit_ep
    return exits


def parse_season_winners(wikitext):
    """
    Extract authoritative list of championship-winning player names from the
    season's `InfoboxSeason` template `|winner = ...` field.

    Necessary because some team-format seasons (e.g. The Ruins S18, The
    Inferno 3 S14) put the team name ("Champions"/"Challengers") in the
    cast table where the finish cell would otherwise live — so cast-table-
    derived finish text never catches that the team won. The infobox
    `|winner` field is the canonical source.

    Returns a list of canonical player wikilink-target names.
    """
    if not wikitext:
        return []
    code = mwp.parse(wikitext)
    for tpl in code.filter_templates():
        tname = str(tpl.name).strip().lower()
        if "infobox" in tname and ("season" in tname or "challenge" in tname):
            for p in tpl.params:
                if str(p.name).strip().lower() == "winner":
                    value = str(p.value)
                    # Wikilink target before any | (display alias)
                    return [m.split("|")[0].strip()
                            for m in re.findall(r"\[\[([^\]]+)\]\]", value)]
    return []


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

    # S39 Battle for a New Champion put a "===Champions===" sub-heading
    # under Contestants for 10 mercenary vets who couldn't win the title.
    # Their tables have no Finish column. Detect that boundary so we can
    # tag those players "Champion Mercenary" instead of leaving blank.
    champion_section_start = -1
    m = re.search(r"^=+\s*Champions?\s*=+\s*$", sec, re.MULTILINE)
    if m:
        champion_section_start = m.start()

    code = mwp.parse(sec)
    rows = []
    seen_players_this_section = set()
    row_counter = 0  # monotonic, used to build pair_id
    table_search_start = 0

    for table in _iter_wikitables(code):
        # Skip wrapper tables: they contain nested {| ... |} blocks
        inner_body = table[2:-2]
        if "{|" in inner_body and "|}" in inner_body:
            continue

        # Locate this table in the source section text (for subsection lookup).
        idx = sec.find(table, table_search_start)
        if idx >= 0:
            table_search_start = idx + len(table)
        in_champions_subsection = (
            champion_section_start >= 0 and idx >= champion_section_start
        )

        caption = _table_caption(table)
        table_gender = _caption_gender(caption)
        team_name = _team_name_from_caption(caption, table_gender)
        # Detect "Male partner | Female partner | Finish" pair-table format
        # (used by Battle of the Exes, Rivals, etc.). When found, gender
        # comes from which COLUMN a player's icon sits in, not from caption.
        pair_cols = None if table_gender else _detect_pair_partner_columns(table)

        for cells in _iter_table_rows(table):
            row_counter += 1
            extracted = _extract_contestants_from_row(cells, table_gender, pair_cols)
            # Multi-player rows become a pair group (pair seasons).
            # Single-player rows have no pair_id.
            pair_id = f"row_{row_counter:03d}" if len(extracted) > 1 else ""
            for row in extracted:
                if row["player"] in seen_players_this_section:
                    continue
                seen_players_this_section.add(row["player"])
                row["team"] = team_name
                row["pair_id"] = pair_id
                if in_champions_subsection and not row.get("finish"):
                    row["finish"] = "Champion Mercenary"
                rows.append(row)
    return rows


def _extract_contestants_from_row(cells, table_gender, pair_cols=None):
    """
    From one table row, extract one or more contestant records.
    A row may contain 1 player (individual format) or 2+ players (pair/team
    format where partners share a row). All players in the row share the
    row's finish text.

    pair_cols: optional (m_end, f_end) tuple from _detect_pair_partner_columns.
    When set, gender is derived from each player's COLUMN position (M if in
    cols [0, m_end), F if in cols [m_end, f_end)) instead of table_gender.
    """
    if len(cells) < 2:
        return []

    # Find all player names from File-icon link= params, dedup-preserving order.
    # If pair_cols is set, also record each player's gender via column position.
    seen = set()
    players = []  # list of (name, gender_from_col_or_None)
    col = 0
    for cell in cells:
        span = _cell_colspan(cell)
        cell_gender = ""
        if pair_cols is not None:
            m_end, f_end = pair_cols
            # A cell can span multiple columns; use its START column for attribution.
            if col < m_end:        cell_gender = "M"
            elif col < f_end:      cell_gender = "F"
        for name in _players_from_icons(cell):
            if name not in seen:
                seen.add(name)
                players.append((name, cell_gender))
        col += span

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
        # Strip residual HTML tags and wikicode markers (e.g. unclosed
        # <small> in S36/S38/S39/S41 finish cells where the closing
        # </small> is missing — strip_code leaves "<small>''" between
        # "Eliminated" and "in", breaking the substring match).
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain = re.sub(r"''+", " ", plain)
        plain = " ".join(plain.split())
        if not plain:
            continue
        pl = plain.lower()
        # Be precise here — "champion" and "finalist" as bare keywords match
        # bio text like "1x champion" in player cells. Use phrases or
        # leading-position-anchored matches that only fire on actual finish
        # cell content.
        if not finish and (
            re.search(r"\bwinners?\b", pl)
            or re.search(r"\brunners?[- ]?up\b", pl)
            or re.search(r"\b(third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth)\s+place\b", pl)
            or re.search(r"\bbottom\s+(two|three|four|five|six|seven|eight|\d+)\b", pl)
            or "eliminated in" in pl
            or "disqualif" in pl
            or pl.startswith("quit")
            or "withdrew" in pl
            or "ejected" in pl
            or "did not compete" in pl
            or "medically" in pl
        ):
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
            "gender": (g or table_gender or ""),
            "finish": finish,
            "origin": origin_pool[i] if i < len(origin_pool) else "",
        }
        for i, (p, g) in enumerate(players)
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

    Each row is annotated with `_native_len` (int) — the number of cells
    that came from THIS physical row, before rowspan content was appended.
    Downstream parsers that care about positional anchors (winner/loser
    is the last 2 cells of THIS row) should use cells[:_native_len].
    """
    pending = []  # list of [content, remaining_rows]
    out = []
    for row in rows:
        # Expire any pending rowspan cells that have run out
        pending = [p for p in pending if p[1] > 0]
        # Append still-active rowspan content to this row
        native_len = len(row)
        augmented = list(row) + [p[0] for p in pending]
        # Attach native_len as an attribute on the list so cells[:n] gives
        # only this row's own content (excluding rowspan-appended content
        # from prior rows above).
        augmented = _RowList(augmented, native_len=native_len)
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


class _RowList(list):
    """List subclass that carries `_native_len` for native-row slicing."""
    def __new__(cls, iterable, native_len):
        obj = super().__new__(cls)
        return obj

    def __init__(self, iterable, native_len):
        super().__init__(iterable)
        self._native_len = native_len


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
    """Fallback: first non-File wikilink in a cell.
    Strips <ref> tags first so footnote wikilinks (e.g. `[[Mercenary|shocking
    twist]]` in S20 Cutthroat's "Back Up Off Me" ref) don't leak as players."""
    body = _strip_cell_attrs(cell)
    code = mwp.parse(body)
    for tag in code.filter_tags(matches=lambda t: str(t.tag).lower() == "ref"):
        code.remove(tag)
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


def parse_game_summary_individual(wikitext, team_rosters=None, exit_episodes=None):
    """
    Parse the elimination chart for individual-format seasons.
    Robust to varying column counts (Duel: 10 cols, Total Madness: 13 cols, etc.)
    by anchoring on first cells (episode/challenge/gender) and last cells
    (winner/loser). Returns (eliminations, dailies).

    `team_rosters` (optional) is a dict {team_name: [player_list]} from the
    season's cast table. When provided, daily-winner cells containing only
    a team-name text (S20 Cutthroat-style: "Red Team" / "Grey Team" with no
    player icons) are expanded into per-roster-member daily rows.

    `exit_episodes` (optional) is a dict {player: exit_episode_int} from
    Episode Progress (players who QUIT / MED-DQ / withdrew mid-season).
    Used to filter team-roster expansions so they don't credit players who
    had already exited before that team's win.
    """
    sec = get_section(wikitext, "Game Summary")
    if not sec:
        return [], []
    team_rosters = team_rosters or {}
    team_lower = {k.lower(): v for k, v in team_rosters.items()}
    exit_episodes = exit_episodes or {}

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

    # S16 The Island uses a "Face-off" chart format whose outcome columns
    # [Winner | Key Stolen | Saved | Eliminated] don't map cleanly to binary
    # H2H — the chart shows 3-4 nominees per face-off with no single named
    # opponent. Detected for future reference, but currently parsed via the
    # default last-2-cells logic (which is wrong but at least non-misleading).
    is_face_off_chart = "Face-off nominees" in elim_table and "Key Stolen" in elim_table

    eliminations = []
    dailies = []
    last_episode = ""  # carried forward for continuation rows (gender-only first cell)
    # Track players already eliminated to filter team-roster daily expansions
    # (S20 Grey Team won ep 1-2; Shauvon quit ep 3 yet had been credited for
    # later Grey wins until we filtered her out here).
    eliminated_so_far = set()

    for cells in _iter_table_rows(elim_table):
        # Minimum 3 cells: typically episode + game + winner + loser (4),
        # but some continuation rows (S29 X-It "Bloodbath", S27 finale stage
        # multi-elim rows) compress to {pair-context, winner, loser} = 3.
        # Below 3 is never enough for a binary H2H.
        if len(cells) < 3:
            continue
        first_plain = _cell_plain(cells[0])
        # Skip the chart header row ("Episode" / "#" / "Elimination chart")
        # but NOT continuation rows whose first cell is empty (style-only)
        # or has colspan attributes — those are legitimate sub-rows in
        # multi-row episodes (S29 X-It ep 11/12, S27 Final-stage events).
        if first_plain.lower() in ("episode", "#"):
            continue
        if "elimination chart" in cells[0].lower():
            continue

        # Detect continuation row: first cell isn't a real episode token.
        # Episode column often has rowspan=N (especially in modern multi-elim
        # charts like S40); continuation rows then start with a player icon,
        # an "Era I/II" label, a gender label, etc. — never a number/range.
        # A real episode is short and starts with a digit (or is "M"/"F"
        # under team-format charts that prefix with gender).
        first_lower = first_plain.lower()
        looks_like_episode = bool(re.match(r'^\s*\d', first_plain))
        is_continuation = (
            not looks_like_episode
            or first_lower in ("male", "female")
            or _is_player_cell(cells[0])
        )
        episode = last_episode if is_continuation else first_plain
        if not is_continuation:
            last_episode = episode

        # For positional winner/loser detection, look only at cells from
        # THIS physical row (exclude rowspan-appended content bled in from
        # prior rows above — e.g. S30 Dirty 30 "The Reel World" had Jenna
        # Compono's icon bleeding into the men's elim row).
        native_len = getattr(cells, "_native_len", len(cells))

        # Skip rows where the elimination collapses to N/A. Check only the
        # FINAL cell of the native row — that's the "Eliminated" column. A
        # mid-row N/A (e.g. S18 ep 8/9 row 18 where "Voted In" was N/A but
        # Bananas-vs-Dunbar still happened) doesn't mean elim was skipped.
        # Rowspan-bled cells from prior rows above are also excluded by
        # native_len slicing.
        last_cell = cells[native_len - 1] if native_len > 0 else ""
        elim_skipped = ("N/A" in last_cell and not _is_player_cell(last_cell))
        player_idx = [i for i, c in enumerate(cells[:native_len]) if _is_player_cell(c)]
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
            # Bloodline-pair format detection (S27 Battle of the Bloodlines):
            # the LAST player cell is the eliminated bloodline PAIR, but
            # the immediately-preceding player cell is just the SOLO loser
            # (one half of that pair). The actual winner is one step back.
            # Pattern: last cell has 2+ players AND second-to-last has 1
            # player AND that player is inside the last cell's pair.
            if len(player_idx) >= 3:
                last_players = _players_from_icons(cells[loser_i]) or []
                penult_players = _players_from_icons(cells[winner_i]) or []
                if (len(last_players) >= 2
                    and len(penult_players) == 1
                    and penult_players[0] in last_players):
                    winner_i = player_idx[-3]
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
            # Mark losers as eliminated from this episode forward, so
            # team-roster daily expansions later in the chart don't credit
            # players who already exited the show.
            for l in loser_players:
                if l:
                    eliminated_so_far.add(l)
            daily_range_end = winner_i
        else:
            daily_range_end = len(cells)

        # Daily winners — three chart shapes:
        #   1) Team-format with team-NAME text at cell[2] (S20 Cutthroat:
        #      "Red Team"/"Grey Team"/"Blue Team"; no player icons in the
        #      Winners cell). The cells that DO have icons after position 2
        #      are gulag/zone NOMINEES, not winners. Expand the team's
        #      cast roster instead.
        #   2) Pair-format with no Gender column at cell[2] (S19/S22/S26/
        #      S28/S38/S41 etc.). cell[2] is the Winners pair; cells after
        #      it are dome/exile/zone NOMINEES — emit only ONE row.
        #   3) Individual gender-split charts (S35/S40 etc.). cell[2] is a
        #      "M"/"F" gender label; cells[3] and [4] are prize/safety.
        team_roster_for_row = None
        cell2_is_player = len(cells) > 2 and _is_player_cell(cells[2])
        # Scan the leading text cells (2 and 3) for a known team name. S20
        # Cutthroat puts the team name at cell[2]; S15 Gauntlet III etc.
        # put a Gender label at cell[2] and the team name at cell[3].
        # Look for a team name. For double-daily formats like S15 Gauntlet III
        # the team-name cell sits on the continuation row too (each row has
        # its own female daily challenge with its own winning team), so the
        # lookup must run for both row types. To avoid mis-firing on S20-
        # Cutthroat continuation rows (where the team-name cell is bled in
        # by rowspan from the parent row, not actually a new daily), only
        # check NATIVE cells on continuation rows.
        if team_lower:
            scan_limit = native_len if is_continuation else len(cells)
            for cand_idx in range(2, min(5, scan_limit)):
                if _is_player_cell(cells[cand_idx]):
                    continue
                text = _cell_plain(cells[cand_idx]).strip().lower()
                if text in team_lower:
                    team_roster_for_row = team_lower[text]
                    break

        if team_roster_for_row:
            # Current episode # for exit-time comparison. Take the leading
            # integer from strings like "1", "8/9", "10/11", "Finale".
            ep_num_m = re.match(r'(\d+)', str(episode))
            current_ep_num = int(ep_num_m.group(1)) if ep_num_m else None
            for p in team_roster_for_row:
                if p in eliminated_so_far:
                    continue  # already lost an elim before this row
                exit_ep = exit_episodes.get(p)
                if exit_ep is not None and current_ep_num is not None and current_ep_num > exit_ep:
                    continue  # quit/MED-DQ'd before this episode
                dailies.append({
                    "episode": episode,
                    "challenge": challenge,
                    "format": "team",
                    "role": "prize",
                    "winner": p,
                })
        else:
            # Bug 1 fix (S20 Cutthroat-style): in team-format seasons (where
            # the daily winner is named by team and we expand from a cast
            # roster on row 1), the continuation row holds female-side gulag
            # NOMINEES, not daily winners. Suppress emission there entirely.
            if is_continuation and team_lower:
                pass  # skip — nominee data, not daily winners
            else:
                if is_continuation:
                    start_idx = 1; n_emit = 2
                elif cell2_is_player:
                    start_idx = 2; n_emit = 1
                else:
                    start_idx = 3; n_emit = 2

                daily_cell_idx = [i for i in player_idx if start_idx <= i < daily_range_end]
                roles = ["prize", "safety"]
                # Bug 2 fix (S23 BotS-style): the "Nominated pair" / "Last-place
                # pair" cells get mis-labeled as prize/safety winners. Drop any
                # daily-winner players who are ALSO arena participants in this
                # same row — by show rules, daily prize/safety means you avoid
                # arena, so you can't be both daily winner AND arena player.
                same_row_arena = set()
                if not elim_skipped:
                    same_row_arena = set(winner_players) | set(loser_players)
                for j, idx in enumerate(daily_cell_idx[:n_emit]):
                    cell_players = _players_from_icons(cells[idx])
                    if not cell_players:
                        continue
                    n = len(cell_players)
                    fmt = "individual" if n == 1 else ("pair" if n == 2 else "team")
                    role = roles[j] if j < len(roles) else "winner"
                    if same_row_arena and role in ("prize", "safety"):
                        cell_players = [p for p in cell_players if p not in same_row_arena]
                        if not cell_players:
                            continue
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

    # Authoritative championship-winner list from the season's infobox.
    # Override the cast-table finish for these players ONLY IF the cast
    # table didn't already give them a proper "Winners in <episode>" line
    # (team-format seasons whose cast table uses team names as captions
    # never give players an explicit Winner finish, so the infobox is the
    # only source). We preserve any existing "Winners in <X>" text so the
    # episode name doesn't get clobbered for pair/individual seasons.
    winners = set(parse_season_winners(wt))
    if winners:
        # Reuse an existing well-formed Winners label if any contestant has one
        existing_winners_label = ""
        for c in contestants:
            f = str(c.get("finish") or "")
            if re.match(r"^Winners?\s+in\b", f, re.IGNORECASE):
                existing_winners_label = f
                break
        fallback_label = existing_winners_label or "Winners"
        for c in contestants:
            if c.get("player") in winners:
                f = str(c.get("finish") or "")
                if not re.match(r"^Winners?\s+in\b", f, re.IGNORECASE):
                    c["finish"] = fallback_label

    df_c = pd.DataFrame(contestants)
    df_c.insert(0, "season_id", season_id)
    df_c.to_csv(season_dir / "contestants.csv", index=False)

    # Build team rosters for team-format elim charts (S20 Cutthroat puts
    # "Red Team" / "Grey Team" / "Blue Team" text in the daily-winners cell
    # instead of player icons — we expand to roster via this map).
    team_rosters = {}
    cast_players = []
    for c in contestants:
        cast_players.append(c["player"])
        t = str(c.get("team") or "").strip()
        if t:
            team_rosters.setdefault(t, []).append(c["player"])

    # Merge in any season-level manual team-roster overrides. These are
    # necessary when the cast page doesn't expose explicit team labels but
    # the elim chart's daily-winner column uses team names (BotS-style
    # seasons whose teams group by Real World city, era, franchise, etc.).
    # Manual entries take precedence on conflicts so we can fix mis-parsed
    # cast entries (S40 Era assignments leaking origin URLs into team).
    manual = MANUAL_TEAM_ROSTERS.get(season_id)
    if manual:
        for tn, players in manual.items():
            team_rosters[tn] = list(players)

    # Players who exited mid-season (QUIT / MED-DQ / withdrew) per Episode
    # Progress table — filters out over-credits in team-roster expansions
    # (S15 Coral, S20 Shauvon).
    exit_episodes = parse_episode_progress(wt, cast_players=cast_players)

    eliminations, dailies = parse_game_summary_individual(
        wt, team_rosters=team_rosters, exit_episodes=exit_episodes
    )
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
