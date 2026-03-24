"""
DFW Deal Agent — Monitoring Dashboard
======================================
Lightweight Flask dashboard for monitoring the RE bot.
Run: python dashboard.py
Open: http://localhost:5050
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from flask import Flask, render_template_string

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deals.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── SCRAPER REGISTRY (mirrors main.py) ──────────────────────────────────────
SCRAPERS = {
    "buildout": {"label": "Buildout (Greysteel, SVN, Lee)", "enabled": True},
    "crexi": {"label": "Crexi", "enabled": False, "reason": "Login button not found"},
    "loopnet": {"label": "LoopNet", "enabled": False, "reason": "Akamai blocks detail fetch"},
    "zillow": {"label": "Zillow", "enabled": False, "reason": "API endpoint changed (404)"},
    "redfin": {"label": "Redfin", "enabled": False, "reason": "0 results"},
    "multifamily_group": {"label": "Multifamily Group", "enabled": False, "reason": "403 blocked"},
    "silva_multifamily": {"label": "Silva Multifamily", "enabled": False, "reason": "404 all pages"},
    "ipa_texas": {"label": "IPA Texas", "enabled": False, "reason": "No cards found"},
    "rentcast": {"label": "RentCast", "enabled": False, "reason": "No API key"},
}


@app.route("/")
def index():
    conn = get_db()

    # Run history (last 20)
    runs = conn.execute("SELECT * FROM run_log ORDER BY run_at DESC LIMIT 20").fetchall()
    runs = [dict(r) for r in runs]
    for r in runs:
        r["sources_scraped"] = json.loads(r["sources_scraped"] or "[]")
        r["errors"] = json.loads(r["errors"] or "[]")
        try:
            dt = datetime.fromisoformat(r["run_at"])
            r["run_at_fmt"] = dt.strftime("%b %d, %I:%M %p")
            r["run_at_ago"] = _time_ago(dt)
        except Exception:
            r["run_at_fmt"] = r["run_at"]
            r["run_at_ago"] = ""

    # Deals
    deals = conn.execute(
        "SELECT * FROM deals WHERE is_active=1 ORDER BY last_seen DESC"
    ).fetchall()
    deals = [dict(d) for d in deals]

    qualified_deals = [d for d in deals if d["passes_hurdle"]]
    contact_broker_deals = [d for d in deals if (d["price"] or 0) <= 0]
    priced_deals = [d for d in deals if (d["price"] or 0) > 0]

    # Stats
    total_runs = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
    total_deals_ever = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    total_alerts = conn.execute(
        "SELECT SUM(alert_count) FROM deals WHERE alert_count > 0"
    ).fetchone()[0] or 0

    last_run = runs[0] if runs else None
    last_run_time = datetime.fromisoformat(last_run["run_at"]) if last_run else None

    # Bot health
    if last_run_time:
        hours_since = (datetime.utcnow() - last_run_time).total_seconds() / 3600
        if hours_since < 14:
            bot_status = "healthy"
            bot_label = "Running"
        elif hours_since < 26:
            bot_status = "warning"
            bot_label = "Delayed"
        else:
            bot_status = "error"
            bot_label = "Down"
    else:
        bot_status = "error"
        bot_label = "No runs"

    conn.close()

    return render_template_string(
        TEMPLATE,
        runs=runs,
        deals=deals,
        qualified_deals=qualified_deals,
        contact_broker_deals=contact_broker_deals,
        priced_deals=priced_deals,
        total_runs=total_runs,
        total_deals_ever=total_deals_ever,
        total_alerts=total_alerts,
        last_run=last_run,
        bot_status=bot_status,
        bot_label=bot_label,
        scrapers=SCRAPERS,
        now=datetime.utcnow(),
    )


@app.route("/run-now")
def run_now():
    """Trigger a manual run (import and execute)."""
    from main import run_agent
    stats = run_agent()
    return render_template_string("""
    <html><head><meta http-equiv="refresh" content="2;url=/"></head>
    <body style="font-family:sans-serif;padding:40px;text-align:center">
    <h2>Run complete</h2>
    <p>Found: {{s.deals_found}} · Analyzed: {{s.deals_analyzed}} · Qualified: {{s.deals_qualified}} · Alerted: {{s.deals_alerted}}</p>
    <p>Redirecting...</p>
    </body></html>
    """, s=stats)


def _time_ago(dt):
    diff = datetime.utcnow() - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds/60)}m ago"
    if seconds < 86400:
        return f"{int(seconds/3600)}h ago"
    return f"{int(seconds/86400)}d ago"


# ── HTML TEMPLATE ────────────────────────────────────────────────────────────

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DFW Deal Agent — Dashboard</title>
<meta http-equiv="refresh" content="300">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f1117; color: #e0e0e0; padding: 24px;
  }
  a { color: #60a5fa; text-decoration: none; }
  a:hover { text-decoration: underline; }

  .header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 24px; flex-wrap: wrap; gap: 12px;
  }
  .header h1 { font-size: 24px; font-weight: 700; color: #fff; }
  .header .subtitle { font-size: 13px; color: #888; margin-top: 4px; }
  .run-btn {
    background: #2563eb; color: #fff; border: none; padding: 10px 20px;
    border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer;
    transition: background 0.2s;
  }
  .run-btn:hover { background: #1d4ed8; }

  /* Status dot */
  .status-dot {
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
    margin-right: 6px; vertical-align: middle;
  }
  .status-dot.healthy { background: #22c55e; box-shadow: 0 0 8px #22c55e88; }
  .status-dot.warning { background: #eab308; box-shadow: 0 0 8px #eab30888; }
  .status-dot.error   { background: #ef4444; box-shadow: 0 0 8px #ef444488; }

  /* Cards grid */
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card {
    background: #1a1d27; border-radius: 12px; padding: 20px;
    border: 1px solid #2a2d3a;
  }
  .card .label { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 6px; }
  .card .value { font-size: 28px; font-weight: 800; color: #fff; }
  .card .sub { font-size: 12px; color: #666; margin-top: 4px; }

  /* Sections */
  .section {
    background: #1a1d27; border-radius: 12px; padding: 20px;
    border: 1px solid #2a2d3a; margin-bottom: 24px;
  }
  .section h2 { font-size: 16px; font-weight: 700; margin-bottom: 16px; color: #fff; }

  /* Tables */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #888; font-weight: 600; font-size: 11px;
       text-transform: uppercase; letter-spacing: 0.5px; padding: 8px 12px;
       border-bottom: 1px solid #2a2d3a; }
  td { padding: 10px 12px; border-bottom: 1px solid #1f2230; vertical-align: top; }
  tr:hover td { background: #1f2230; }

  /* Badges */
  .badge {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 11px; font-weight: 600;
  }
  .badge.green { background: #16533022; color: #22c55e; border: 1px solid #22c55e44; }
  .badge.yellow { background: #85680622; color: #eab308; border: 1px solid #eab30844; }
  .badge.red { background: #7f1d1d22; color: #ef4444; border: 1px solid #ef444444; }
  .badge.blue { background: #1e3a5f22; color: #60a5fa; border: 1px solid #60a5fa44; }
  .badge.gray { background: #33333344; color: #888; border: 1px solid #55555544; }

  /* Scraper grid */
  .scraper-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
  .scraper-item {
    display: flex; align-items: center; gap: 10px;
    padding: 12px 16px; border-radius: 8px; background: #12141c;
    border: 1px solid #2a2d3a;
  }
  .scraper-item .name { font-weight: 600; font-size: 14px; }
  .scraper-item .reason { font-size: 12px; color: #666; }

  /* Error row */
  .error-text { color: #ef4444; font-size: 12px; }

  /* Responsive */
  @media (max-width: 768px) {
    .cards { grid-template-columns: 1fr 1fr; }
    body { padding: 12px; }
  }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div>
    <h1>
      <span class="status-dot {{ bot_status }}"></span>
      DFW Deal Agent
    </h1>
    <div class="subtitle">
      {{ bot_label }}
      {% if last_run %} · Last run: {{ last_run.run_at_fmt }} ({{ last_run.run_at_ago }}){% endif %}
      · Cron: 7:00 AM & 7:00 PM daily
    </div>
  </div>
  <a href="/run-now" class="run-btn" onclick="this.textContent='Running...'; this.style.opacity=0.6">
    Run Now
  </a>
</div>

<!-- Stats Cards -->
<div class="cards">
  <div class="card">
    <div class="label">Total Runs</div>
    <div class="value">{{ total_runs }}</div>
    <div class="sub">Since first deployment</div>
  </div>
  <div class="card">
    <div class="label">Active Deals</div>
    <div class="value">{{ deals|length }}</div>
    <div class="sub">{{ priced_deals|length }} priced · {{ contact_broker_deals|length }} contact broker</div>
  </div>
  <div class="card">
    <div class="label">Qualifying</div>
    <div class="value" style="color: {% if qualified_deals %}#22c55e{% else %}#888{% endif %}">{{ qualified_deals|length }}</div>
    <div class="sub">Pass all financial hurdles</div>
  </div>
  <div class="card">
    <div class="label">Alerts Sent</div>
    <div class="value">{{ total_alerts }}</div>
    <div class="sub">All-time email alerts</div>
  </div>
  <div class="card">
    <div class="label">Scrapers Active</div>
    <div class="value" style="color: #eab308">{{ scrapers.values()|selectattr('enabled')|list|length }} / {{ scrapers|length }}</div>
    <div class="sub">{{ (scrapers|length) - (scrapers.values()|selectattr('enabled')|list|length) }} disabled</div>
  </div>
</div>

<!-- Last Run Details -->
{% if last_run %}
<div class="section">
  <h2>Last Run Summary</h2>
  <div class="cards" style="margin-bottom:0">
    <div class="card" style="background:#12141c">
      <div class="label">Listings Found</div>
      <div class="value">{{ last_run.deals_found }}</div>
      <div class="sub">From {{ last_run.sources_scraped|join(', ') or 'no sources' }}</div>
    </div>
    <div class="card" style="background:#12141c">
      <div class="label">Analyzed</div>
      <div class="value">{{ last_run.deals_analyzed }}</div>
      <div class="sub">Had price data for analysis</div>
    </div>
    <div class="card" style="background:#12141c">
      <div class="label">Qualified</div>
      <div class="value" style="color:{% if last_run.deals_qualified > 0 %}#22c55e{% else %}#888{% endif %}">{{ last_run.deals_qualified }}</div>
      <div class="sub">Met financial hurdles</div>
    </div>
    <div class="card" style="background:#12141c">
      <div class="label">Alerts Sent</div>
      <div class="value">{{ last_run.deals_alerted }}</div>
    </div>
    <div class="card" style="background:#12141c">
      <div class="label">Duration</div>
      <div class="value">{{ "%.1f"|format(last_run.duration_seconds) }}s</div>
    </div>
  </div>
  {% if last_run.errors %}
  <div style="margin-top:12px">
    {% for e in last_run.errors %}
    <div class="error-text">{{ e }}</div>
    {% endfor %}
  </div>
  {% endif %}
</div>
{% endif %}

<!-- Run History -->
<div class="section">
  <h2>Run History</h2>
  <table>
    <thead>
      <tr>
        <th>When</th>
        <th>Found</th>
        <th>Analyzed</th>
        <th>Qualified</th>
        <th>Alerted</th>
        <th>Sources</th>
        <th>Duration</th>
        <th>Errors</th>
      </tr>
    </thead>
    <tbody>
      {% for r in runs %}
      <tr>
        <td title="{{ r.run_at }}">{{ r.run_at_fmt }}<br><span style="color:#666;font-size:11px">{{ r.run_at_ago }}</span></td>
        <td>{{ r.deals_found }}</td>
        <td>{{ r.deals_analyzed }}</td>
        <td>
          {% if r.deals_qualified > 0 %}<span class="badge green">{{ r.deals_qualified }}</span>
          {% else %}<span style="color:#666">0</span>{% endif %}
        </td>
        <td>
          {% if r.deals_alerted > 0 %}<span class="badge blue">{{ r.deals_alerted }}</span>
          {% else %}<span style="color:#666">0</span>{% endif %}
        </td>
        <td>
          {% for s in r.sources_scraped %}<span class="badge gray">{{ s }}</span> {% endfor %}
        </td>
        <td>{{ "%.1f"|format(r.duration_seconds) }}s</td>
        <td>
          {% if r.errors %}
            {% for e in r.errors %}<span class="error-text">{{ e }}</span><br>{% endfor %}
          {% else %}<span style="color:#666">—</span>{% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<!-- Active Deals -->
<div class="section">
  <h2>Active Deals ({{ deals|length }})</h2>
  {% if deals %}
  <table>
    <thead>
      <tr>
        <th>Address</th>
        <th>City</th>
        <th>Units</th>
        <th>Price</th>
        <th>Status</th>
        <th>Source</th>
        <th>First Seen</th>
        <th>Last Seen</th>
      </tr>
    </thead>
    <tbody>
      {% for d in deals %}
      <tr>
        <td>
          {% if d.url %}<a href="{{ d.url }}" target="_blank">{{ d.address or 'Unknown' }}</a>
          {% else %}{{ d.address or 'Unknown' }}{% endif %}
        </td>
        <td>{{ d.city or '—' }}, {{ d.state or 'TX' }}</td>
        <td>{{ d.units or '—' }}</td>
        <td>
          {% if d.price and d.price > 0 %}
            ${{ "{:,.0f}".format(d.price) }}
          {% else %}
            <span class="badge yellow">Contact Broker</span>
          {% endif %}
        </td>
        <td>
          {% if d.passes_hurdle %}
            <span class="badge green">Qualifies</span>
            {% if d.ai_recommendation %}<span class="badge blue">{{ d.ai_recommendation }}</span>{% endif %}
          {% elif d.price and d.price > 0 %}
            <span class="badge red">No-Go</span>
          {% else %}
            <span class="badge gray">No Price</span>
          {% endif %}
        </td>
        <td><span class="badge gray">{{ d.source or '—' }}</span></td>
        <td style="font-size:12px;color:#888">{{ d.first_seen[:10] if d.first_seen else '—' }}</td>
        <td style="font-size:12px;color:#888">{{ d.last_seen[:10] if d.last_seen else '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:#666">No active deals tracked yet.</p>
  {% endif %}
</div>

<!-- Scraper Status -->
<div class="section">
  <h2>Scraper Status</h2>
  <div class="scraper-grid">
    {% for key, s in scrapers.items() %}
    <div class="scraper-item">
      <span class="status-dot {{ 'healthy' if s.enabled else 'error' }}"></span>
      <div>
        <div class="name">{{ s.label }}</div>
        {% if not s.enabled %}<div class="reason">{{ s.reason }}</div>{% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<!-- Footer -->
<div style="text-align:center; color:#555; font-size:12px; margin-top:32px; padding:16px">
  SET Holdings · DFW Deal Agent · Auto-refreshes every 5 min
</div>

</body>
</html>
"""


if __name__ == "__main__":
    print("\n  DFW Deal Agent — Dashboard")
    print("  http://localhost:5050\n")
    app.run(host="0.0.0.0", port=5050, debug=True)
