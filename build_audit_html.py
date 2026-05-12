# =========================================================
# LAVIN — build the audit HTML report from data/audit_report.csv.
#
# IMPORTANT correction vs. naive interpretation:
#   Fandom's `challengewins` field is total DAILY CHALLENGES WON (career
#   stat), NOT championship season count. The mismatch on this field is
#   not a data bug — our championship count comes from parsing the
#   `finish` field for "Winner".
#
# What this report focuses on:
#   1. Players with elim-count differences ≥ 3 between us and Fandom.
#      Fandom's elim stat is sometimes scope-narrower than ours (team-
#      format gauntlets often don't count for Fandom but DO appear in
#      our chart). Worth eyeballing for genuine over/under-counts.
#   2. Players entirely missing from Fandom (probably parser artifacts).
#   3. Season-count differences — Fandom counts ALL series (Champs vs.
#      Pros etc.), we count only the main S5-S41 window.
# =========================================================
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = DATA / "audit.html"


def main():
    df = pd.read_csv(DATA / "audit_report.csv").fillna("")
    # Compute delta columns; treat blanks as 0 for the math
    for col in ["fandom_elim_wins", "fandom_elim_losses", "our_elim_wins",
                "our_elim_losses", "fandom_seasons", "our_seasons"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["elimW_delta"] = (df["our_elim_wins"].fillna(0) - df["fandom_elim_wins"].fillna(0))
    df["elimL_delta"] = (df["our_elim_losses"].fillna(0) - df["fandom_elim_losses"].fillna(0))
    df["abs_elim_delta"] = df[["elimW_delta", "elimL_delta"]].abs().sum(axis=1)

    no_infobox = df[df["status"] == "no_infobox"]
    elim_diffs = df[df["abs_elim_delta"] >= 3].sort_values("abs_elim_delta", ascending=False)
    season_diffs = df[(df["fandom_seasons"].notna()) & (df["our_seasons"].notna()) &
                      (df["fandom_seasons"] != df["our_seasons"]) &
                      (df["our_seasons"] > df["fandom_seasons"])].sort_values(
        "our_seasons", ascending=False
    )  # we have MORE seasons than Fandom — that would be the suspicious direction

    def row_html(row, fields):
        cells = []
        for f, lbl in fields:
            v = row.get(f, "")
            if isinstance(v, float) and v != v:  # NaN
                v = ""
            cells.append(f"<td>{v}</td>")
        return "<tr>" + "".join(cells) + "</tr>"

    def table_html(df, fields, caption):
        if not len(df):
            return f"<section><h2>{caption}</h2><p class='note'>None.</p></section>"
        head = "".join(f"<th>{lbl}</th>" for _, lbl in fields)
        body = "".join(row_html(r, fields) for _, r in df.iterrows())
        return f"""
        <section>
          <h2>{caption} ({len(df)})</h2>
          <table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
        </section>
        """

    fields_elim = [
        ("player", "Player"),
        ("our_elim_wins", "Our W"), ("our_elim_losses", "Our L"),
        ("fandom_elim_wins", "Fandom W"), ("fandom_elim_losses", "Fandom L"),
        ("elimW_delta", "ΔW"), ("elimL_delta", "ΔL"),
        ("our_seasons", "Our Sn"), ("fandom_seasons", "Fdm Sn"),
    ]
    fields_seasons = [
        ("player", "Player"),
        ("our_seasons", "Our Sn"), ("fandom_seasons", "Fdm Sn"),
        ("our_elim_wins", "Our EW"), ("our_elim_losses", "Our EL"),
    ]
    fields_missing = [("player", "Player"), ("our_seasons", "Our Sn"),
                      ("our_elim_wins", "EW"), ("our_elim_losses", "EL")]

    total = len(df)
    n_mm = (df["status"] == "mismatch").sum()
    n_ok = (df["status"] == "ok").sum()
    n_no = len(no_infobox)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LAVIN — Player Audit Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #fafafa; color: #222; padding: 28px; max-width: 1200px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
    h2 {{ font-size: 18px; margin: 28px 0 10px 0; padding-bottom: 6px; border-bottom: 2px solid #2c3e50; }}
    .subtitle {{ color: #666; margin-bottom: 18px; font-size: 14px; }}
    .summary {{ background: #fff; border: 1px solid #ddd; padding: 14px 16px;
                border-radius: 8px; margin-bottom: 24px; font-size: 14px; }}
    .summary strong {{ color: #2c3e50; }}
    .note {{ font-style: italic; color: #888; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
             box-shadow: 0 1px 3px rgba(0,0,0,0.07); }}
    th {{ background: #2c3e50; color: #fff; padding: 8px 10px; text-align: left;
          font-size: 12px; font-weight: 600; }}
    td {{ padding: 6px 10px; border-bottom: 1px solid #eee; font-size: 13px;
          font-variant-numeric: tabular-nums; }}
    td:first-child {{ font-weight: 600; }}
  </style>
</head>
<body>
  <h1>LAVIN — Player Audit Report</h1>
  <p class="subtitle">
    Compared 356 event-relevant players against their Fandom infobox truth.
  </p>
  <div class="summary">
    <p><strong>Methodology:</strong> for each player, fetch their Fandom page,
    parse the <code>InfoboxChallenger</code> template, compare to our derived stats.</p>
    <p><strong>Important caveat:</strong> Fandom's <code>challengewins</code> field
    is total <em>daily challenges won</em> across a career, NOT championship count.
    So that field is NOT shown here (it would generate false-alarm mismatches).</p>
    <p><strong>Counts:</strong> {total} audited.
       OK: <strong>{n_ok}</strong> ·
       Some mismatch: <strong>{n_mm}</strong> ·
       No Fandom infobox: <strong>{n_no}</strong></p>
    <p><strong>Reading guide:</strong> Mismatches in season count are usually because
    Fandom counts cross-series appearances (All Stars, Champs vs. Pros, UK Challenge)
    that we don't include. Mismatches in elim count can be definitional: Fandom often
    excludes team-format gauntlets from a player's elim record, while we count every
    H2H matchup in the chart. Use the report to flag <em>genuinely suspicious</em>
    cases — large unexplained gaps in either direction.</p>
  </div>

  {table_html(elim_diffs, fields_elim, "Players with elim-count gap ≥ 3 vs Fandom")}

  {table_html(season_diffs.head(40), fields_seasons,
              "Players where WE have MORE seasons than Fandom (potential data leak)")}

  {table_html(no_infobox, fields_missing, "Players with NO Fandom infobox")}

  <p class="note">Full data: <code>data/audit_report.csv</code></p>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
