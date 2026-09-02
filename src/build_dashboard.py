#!/usr/bin/env python3
"""Build the season standings dashboard and write index.html.

Reads output/season_sim_<league_id>.json (written by main.py) -- run main.py
first (or after) to refresh the underlying numbers, then run this to regenerate
the page. Manually triggered, like the World Cup dashboard's build script: no
auto-refresh, just re-run both whenever you want updated odds.

    python3 main.py && python3 build_dashboard.py
"""
import argparse
import json
from datetime import datetime
from html import escape as esc
from pathlib import Path

DEFAULT_LEAGUE_ID = "1392633709420646400"
OUT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = OUT_DIR / "output"

# ---------------------------------------------------------------------------
# CSS -- dark terminal surface, Inter, heatmap table cells (same component
# vocabulary as the World Cup dashboard, green-tinted rather than blue-tinted).
# ---------------------------------------------------------------------------
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

:root {
  --bg:        #0c1110;
  --surface:   #161d19;
  --surface2:  #1c2420;
  --border:    #2c352e;
  --text:      #e6edf3;
  --muted:     #7d8590;
  --gold:      #b87a1a;
  --green:     #26a06a;
  --amber:     #d29922;
  --red:       #f85149;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* -- Header -- */
header {
  background: linear-gradient(180deg, #10241a 0%, #0c1110 100%);
  border-bottom: 1px solid var(--border);
  padding: 36px 32px 28px;
  text-align: center;
}
header h1 {
  font-size: clamp(1.6rem, 3.5vw, 2.4rem);
  font-weight: 900; letter-spacing: -.6px; line-height: 1.1;
  margin-bottom: 12px;
}
header h1 span { color: var(--green); }
.subtitle {
  color: var(--muted); font-size: 13px; line-height: 1.6;
  max-width: 560px; margin: 0 auto;
}
.updated {
  color: var(--muted); font-size: 11px; margin-top: 10px;
}

/* -- Standings table -- */
.tourn-wrap { max-width: 900px; margin: 0 auto; padding: 26px 14px 40px; }
.tourn-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
.tourn-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 620px; }
.tourn-table th {
  cursor: pointer; padding: 9px 10px; text-align: right; font-weight: 500;
  border-bottom: 2px solid var(--border); background: var(--surface);
  color: var(--muted); white-space: nowrap; user-select: none;
}
.tourn-table th:nth-child(2) { text-align: left; }
.tourn-table th.sort-active { color: var(--text); }
.tourn-table td {
  padding: 9px 10px; text-align: right; border-bottom: 1px solid var(--border); font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.tourn-table td:nth-child(2) { text-align: left; white-space: nowrap; }
.tourn-table tr:last-child td { border-bottom: none; }
.tourn-table tbody tr:hover td { background: var(--surface2) !important; }
.record-inline { color: var(--muted); font-weight: 400; font-size: 12px; }

.playoff-line td { padding: 0; border-bottom: none; }
.playoff-line .line-inner {
  display: flex; align-items: center; gap: 10px; padding: 3px 10px;
  background: var(--surface);
}
.playoff-line .rule { flex: 1; height: 1px; background: var(--border); }
.playoff-line .label {
  font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--muted);
  white-space: nowrap;
}

/* -- Responsive -- */
@media (max-width: 640px) {
  header { padding: 28px 18px 22px; }
}
"""


def heat_bg(val: float, max_val: float, cls: str) -> str:
    """Cell-background heatmap intensity, scaled to this column's max (same
    technique as the World Cup tournament table)."""
    if not val or max_val <= 0:
        return ""
    color = {"green": "38,160,106", "amber": "210,153,34", "red": "248,81,73", "gold": "184,122,26"}[cls]
    intensity = round((val / max_val) * 0.30, 3)  # capped well below opaque -- text stays readable
    return f' style="background:rgba({color},{intensity})"'


def build_html(data: dict) -> str:
    teams = data["teams"]
    league_name = data["league_name"]
    playoff_teams = data["playoff_teams"]
    n_sims = data["n_sims"]

    try:
        generated = datetime.fromisoformat(data["generated_at"]).strftime("%b %d, %Y")
    except Exception:
        generated = data.get("generated_at", "")

    max_playoff = max((t["playoff_pct"] for t in teams), default=1) or 1
    max_bye = max((t["bye_pct"] for t in teams), default=1) or 1
    max_champ = max((t["champ_pct"] for t in teams), default=1) or 1
    max_last = max((t["last_pct"] for t in teams), default=1) or 1

    rows_sorted = sorted(teams, key=lambda t: (-t["avg_final_wins"], -t["avg_final_pts"]))

    body_rows = []
    for i, t in enumerate(rows_sorted, start=1):
        name_cell = f'{esc(t["team"])} <span class="record-inline">({esc(t["record"])})</span>'
        body_rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{name_cell}</td>"
            f'<td>{t["avg_final_wins"]:.1f}</td>'
            f'<td{heat_bg(t["playoff_pct"], max_playoff, "green")}>{t["playoff_pct"]:.1f}%</td>'
            f'<td{heat_bg(t["bye_pct"], max_bye, "green")}>{t["bye_pct"]:.1f}%</td>'
            f'<td{heat_bg(t["champ_pct"], max_champ, "gold")}>{t["champ_pct"]:.1f}%</td>'
            f'<td{heat_bg(t["last_pct"], max_last, "red")}>{t["last_pct"]:.1f}%</td>'
            "</tr>"
        )
        if i == playoff_teams:
            body_rows.append(
                '<tr class="playoff-line"><td colspan="7"><div class="line-inner">'
                '<div class="rule"></div><div class="label">Playoff line</div><div class="rule"></div>'
                "</div></td></tr>"
            )

    parts = [
        "<!doctype html><html lang=\"en\"><head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>{esc(league_name)} &mdash; Season Projections</title>",
        f"<style>{CSS}</style>",
        "</head><body>",
        "<header>",
        f'<h1>{esc(league_name)} <span>&mdash; Playoff Odds</span></h1>',
        f'<p class="subtitle">Based on {n_sims:,} simulations of the season.</p>',
        f'<p class="updated">Last updated {esc(generated)}</p>',
        "</header>",
        '<div class="tourn-wrap">',
        '<div class="tourn-scroll">',
        '<table id="tourn-table" class="tourn-table"><thead><tr>',
        '<th onclick="sortTourn(0)" data-col="0">#</th>',
        '<th onclick="sortTourn(1)" data-col="1">Team</th>',
        '<th onclick="sortTourn(2)" data-col="2" class="sort-active">Proj. Wins &#8595;</th>',
        '<th onclick="sortTourn(3)" data-col="3">Make Playoffs</th>',
        '<th onclick="sortTourn(4)" data-col="4">1st-Round Bye</th>',
        '<th onclick="sortTourn(5)" data-col="5">Champion</th>',
        '<th onclick="sortTourn(6)" data-col="6">Last Place</th>',
        "</tr></thead><tbody>",
        "".join(body_rows),
        "</tbody></table>",
        "</div>",
        "</div>",
        "<script>",
        "var _tDir=-1,_tCol=2;",
        "function sortTourn(col){",
        'var tbl=document.getElementById("tourn-table");',
        'var tbody=tbl.querySelector("tbody");',
        'var rows=Array.from(tbody.querySelectorAll("tr:not(.playoff-line)"));',
        "if(col===_tCol)_tDir*=-1;else{_tDir=-1;_tCol=col;}",
        "rows.sort(function(a,b){",
        'var av=a.cells[col].innerText.replace("%","");',
        'var bv=b.cells[col].innerText.replace("%","");',
        "var an=parseFloat(av),bn=parseFloat(bv);",
        "if(isNaN(an)||isNaN(bn))return _tDir*av.localeCompare(bv);",
        "return _tDir*(bn-an);",
        "});",
        'tbody.querySelectorAll("tr").forEach(function(r){tbody.removeChild(r);});',
        "rows.forEach(function(r,i){r.cells[0].innerText=i+1;tbody.appendChild(r);});",
        'tbl.querySelectorAll("th").forEach(function(th,i){',
        'th.className=i===_tCol?"sort-active":"";',
        'th.innerHTML=th.innerHTML.replace(/[ \\u2191\\u2193]/g,"")+(i===_tCol?(_tDir===-1?" \\u2193":" \\u2191"):"");',
        "});",
        "}",
        "</script>",
        "</body></html>",
    ]
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", default=DEFAULT_LEAGUE_ID)
    args = ap.parse_args()

    json_path = DATA_DIR / f"season_sim_{args.league_id}.json"
    if not json_path.exists():
        raise SystemExit(f"No data at {json_path} -- run main.py first.")
    with open(json_path) as f:
        data = json.load(f)

    html = build_html(data)
    out_path = OUT_DIR / "index.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
