# =========================================================
# LAVIN — all-time top-25 HTML view for PEAK / ERA / ACTIVE
# Output: data/all_time.html
# =========================================================
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = DATA / "all_time.html"
TOP_N = 25
PEAK_MIN_SNAPSHOTS = 30


def main():
    v = pd.read_csv(DATA / "player_views.csv")

    sections = []
    for g, label in [("M", "Men"), ("F", "Women")]:
        v_g = v[v["gender"] == g]

        top_era = v_g.sort_values("era_rating", ascending=False).head(TOP_N).reset_index(drop=True)
        top_peak = v_g[v_g["era_n_snapshots"] >= PEAK_MIN_SNAPSHOTS] \
                       .sort_values("peak_rating", ascending=False).head(TOP_N).reset_index(drop=True)
        top_active = v_g.sort_values("active_rating", ascending=False).head(TOP_N).reset_index(drop=True)

        rows = []
        for i in range(TOP_N):
            cells = [f'<td class="rank-col">#{i+1}</td>']
            for table in [top_era, top_peak, top_active]:
                if i < len(table):
                    row = table.iloc[i]
                    name = row["player"]
                    primary_col = "era_rating" if table is top_era else (
                        "peak_rating" if table is top_peak else "active_rating")
                    primary_val = row[primary_col]
                    if primary_col == "era_rating":
                        primary_str = f"{primary_val:+.1f}"
                    else:
                        primary_str = f"{primary_val:+.2f}"
                    cells.append(f'<td><div class="name">{name}</div>'
                                 f'<div class="meta">{primary_str} · {int(row["era_n_snapshots"])} snaps</div></td>')
                else:
                    cells.append("<td class='empty'>—</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")

        sections.append(f"""
        <section>
          <h2>{label}</h2>
          <table>
            <thead><tr>
              <th class="rank-col">Rank</th>
              <th>ERA (career stature)</th>
              <th>PEAK (best moment)</th>
              <th>ACTIVE (current form)</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </section>
        """)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>LAVIN — All-Time Top {TOP_N}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #fafafa; color: #222; padding: 28px; max-width: 1400px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px 0; font-size: 28px; }}
    h2 {{ font-size: 20px; margin: 32px 0 10px 0; padding-bottom: 6px; border-bottom: 2px solid #2c3e50; }}
    .subtitle {{ color: #666; margin-bottom: 18px; font-size: 14px; }}
    .legend {{ background: #fff; border: 1px solid #ddd; padding: 14px 16px;
               border-radius: 8px; margin-bottom: 24px; font-size: 14px; line-height: 1.5; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
             box-shadow: 0 1px 3px rgba(0,0,0,0.08); table-layout: fixed; }}
    th {{ background: #2c3e50; color: #fff; padding: 10px 12px; text-align: left;
          font-size: 13px; font-weight: 600; }}
    th.rank-col {{ width: 60px; text-align: center; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #eee; font-size: 13px; }}
    td.rank-col {{ text-align: center; color: #888; font-weight: 600; }}
    td.empty {{ color: #ccc; text-align: center; }}
    td .name {{ font-weight: 600; }}
    td .meta {{ font-size: 11px; color: #666; font-variant-numeric: tabular-nums; margin-top: 2px; }}
  </style>
</head>
<body>
  <h1>LAVIN — All-Time Top {TOP_N}</h1>
  <p class="subtitle">Three views derived from one rating timeline.</p>
  <div class="legend">
    <strong>ERA</strong> — cumulative positive rating across the player's career.
    Rewards <em>both quality and longevity</em>. A score of 200 ≈ "held a +1.0 rating
    for 200 snapshots." The all-time greatness view.
    <br><br>
    <strong>PEAK</strong> — the highest rating the player ever achieved at any
    single snapshot. Captures dominant moments. Min {PEAK_MIN_SNAPSHOTS} snapshots
    to filter one-snapshot spikes.
    <br><br>
    <strong>ACTIVE</strong> — the player's rating at their <em>most recent</em>
    snapshot. Reflects current form; retired players show their final rating.
  </div>
  {''.join(sections)}
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
