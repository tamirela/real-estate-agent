"""
Email alert system.
Sends beautifully formatted HTML deal alerts to tamirelazr@gmail.com.
Uses Gmail SMTP (free) or SendGrid (optional).
"""

import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional
from config import EMAIL_CONFIG, API_KEYS

logger = logging.getLogger(__name__)

# Recommendation color mapping
REC_COLORS = {
    "STRONG BUY": "#1a7f4b",
    "BUY": "#2d9e6b",
    "WATCH": "#d97706",
    "PASS": "#dc2626",
}

REC_EMOJI = {
    "STRONG BUY": "🔥",
    "BUY": "✅",
    "WATCH": "👀",
    "PASS": "❌",
}


class EmailAlerter:
    def __init__(self):
        self.config = EMAIL_CONFIG

    def send_deal_alert(
        self,
        deals: list[dict],
        is_price_drop: bool = False,
        run_stats: Optional[dict] = None,
    ) -> bool:
        """Send an email alert for one or more qualifying deals."""
        if not deals:
            return False

        subject = self._build_subject(deals, is_price_drop)
        html = self._build_html(deals, run_stats)
        text = self._build_text(deals)

        return self._send(subject, html, text)

    def send_daily_summary(self, stats: dict, top_deals: list[dict]) -> bool:
        """Send a daily summary of all tracked deals."""
        subject = f"DFW Deal Tracker — Daily Summary ({datetime.now().strftime('%b %d, %Y')})"
        html = self._build_summary_html(stats, top_deals)
        text = f"Daily Summary: {stats.get('qualified_deals', 0)} qualifying deals tracked."
        return self._send(subject, html, text)

    def _build_subject(self, deals: list[dict], is_price_drop: bool) -> str:
        count = len(deals)
        if is_price_drop:
            return f"💰 Price Drop Alert — {count} DFW Deal{'s' if count > 1 else ''} Updated"
        if count == 1:
            d = deals[0]
            rec = d.get("ai_recommendation", "WATCH")
            emoji = REC_EMOJI.get(rec, "📊")
            addr = d.get("address", "Unknown address")
            units = d.get("units", "?")
            coc = d.get("calc_va_coc", 0)
            return f"{emoji} {rec}: {units}-unit DFW Deal @ {addr} — {coc:.1f}% CoC"
        return f"🏢 {count} New DFW Deals Qualify Your Criteria"

    def _build_html(self, deals: list[dict], run_stats: Optional[dict]) -> str:
        cards = "\n".join(self._deal_card_html(d) for d in deals)
        stats_html = self._stats_bar_html(run_stats) if run_stats else ""

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; margin: 0; padding: 20px; color: #1a1a1a; }}
  .container {{ max-width: 680px; margin: 0 auto; }}
  .header {{ background: #1a1a2e; color: white; padding: 24px 28px; border-radius: 12px 12px 0 0; }}
  .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
  .header p {{ margin: 6px 0 0; opacity: 0.7; font-size: 14px; }}
  .card {{ background: white; border-radius: 12px; padding: 24px; margin: 12px 0;
           box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-left: 5px solid #ccc; }}
  .card.strong-buy {{ border-left-color: #1a7f4b; }}
  .card.buy {{ border-left-color: #2d9e6b; }}
  .card.watch {{ border-left-color: #d97706; }}
  .card.pass {{ border-left-color: #dc2626; }}
  .rec-badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px;
               font-size: 12px; font-weight: 700; color: white; margin-bottom: 12px; }}
  .deal-address {{ font-size: 18px; font-weight: 700; margin: 0 0 4px; }}
  .deal-meta {{ color: #666; font-size: 13px; margin-bottom: 16px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }}
  .metric {{ background: #f8f9fa; border-radius: 8px; padding: 12px; text-align: center; }}
  .metric-value {{ font-size: 22px; font-weight: 800; color: #1a1a2e; }}
  .metric-label {{ font-size: 11px; color: #888; margin-top: 2px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .metric-sub {{ font-size: 11px; color: #aaa; margin-top: 2px; }}
  .section-title {{ font-size: 12px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: 0.8px; color: #888; margin: 16px 0 8px; }}
  .one-liner {{ font-size: 15px; color: #333; font-style: italic; margin: 12px 0; padding: 12px;
               background: #f8f9fa; border-radius: 8px; }}
  .summary {{ font-size: 14px; line-height: 1.6; color: #444; margin: 8px 0; }}
  .flag-list {{ margin: 8px 0; padding: 0; list-style: none; }}
  .flag-list li {{ font-size: 13px; padding: 4px 0; color: #555; }}
  .flag-list.red li::before {{ content: "⚠️ "; }}
  .flag-list.green li::before {{ content: "💡 "; }}
  .cta-btn {{ display: inline-block; background: #1a1a2e; color: white;
             padding: 10px 20px; border-radius: 8px; text-decoration: none;
             font-weight: 600; font-size: 14px; margin-top: 16px; }}
  .footer {{ text-align: center; color: #aaa; font-size: 12px; padding: 20px; }}
  .stats-bar {{ background: #e8f4fd; border-radius: 8px; padding: 12px 16px;
               font-size: 13px; color: #444; margin: 8px 0 16px; }}
  .price-drop {{ background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px;
                padding: 8px 12px; font-size: 13px; margin: 8px 0; color: #92400e; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🏢 DFW Deal Alert</h1>
    <p>{len(deals)} deal{'s' if len(deals) > 1 else ''} matching your criteria · {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
  </div>
  {stats_html}
  {cards}
  <div class="footer">
    <p>You're receiving this because a DFW multifamily deal met your criteria:<br>
    30+ units · Under $8M · 20%+ value-add CoC · Class B/C value-add</p>
    <p>Powered by Claude AI + real-time listing data</p>
  </div>
</div>
</body>
</html>"""

    def _deal_card_html(self, d: dict) -> str:
        rec = d.get("ai_recommendation", "WATCH")
        rec_class = rec.lower().replace(" ", "-")
        rec_color = REC_COLORS.get(rec, "#888")
        rec_emoji = REC_EMOJI.get(rec, "📊")

        price = d.get("price", 0)
        units = d.get("units", 0)
        city = d.get("city", "DFW")
        address = d.get("address", "Address not available")
        year = d.get("year_built", "N/A")
        url = d.get("url", "#")
        source = d.get("source", "").replace("_", " ").title()
        dom = d.get("days_on_market")
        dom_str = f"{dom} days on market" if dom else ""
        occ = d.get("occupancy_rate")
        occ_str = f" · {occ*100:.0f}% occupied" if occ else ""

        coc = d.get("calc_va_coc", 0)
        cap = d.get("calc_cap_rate", 0)
        irr = d.get("calc_irr_5yr", 0)
        dscr = d.get("calc_dscr", 0)
        ppu = d.get("price_per_unit", 0) or (price / units if units else 0)
        noi = d.get("calc_noi", 0)
        exit_val = d.get("calc_exit_value", 0)
        eq_mult = d.get("calc_equity_multiple", 0)

        one_line = d.get("ai_one_line", "")
        summary = d.get("ai_summary", "")

        import json as _json
        red_flags = _json.loads(d.get("red_flags", "[]")) if isinstance(d.get("red_flags"), str) else d.get("red_flags", [])
        va_signals = _json.loads(d.get("value_add_signals", "[]")) if isinstance(d.get("value_add_signals"), str) else d.get("value_add_signals", [])
        ai_risks = _json.loads(d.get("ai_risks", "[]")) if isinstance(d.get("ai_risks"), str) else d.get("ai_risks", [])
        ai_opps = _json.loads(d.get("ai_opportunities", "[]")) if isinstance(d.get("ai_opportunities"), str) else d.get("ai_opportunities", [])
        dd = _json.loads(d.get("ai_due_diligence", "[]")) if isinstance(d.get("ai_due_diligence"), str) else d.get("ai_due_diligence", [])

        risks_html = "".join(f"<li>{r}</li>" for r in (ai_risks or red_flags)[:4])
        opps_html = "".join(f"<li>{o}</li>" for o in (ai_opps or va_signals)[:4])
        dd_html = "".join(f"<li>📋 {item}</li>" for item in dd[:3])

        price_drop_html = ""
        if d.get("_price_dropped"):
            old = d.get("_old_price", 0)
            drop_pct = (price - old) / old * 100 if old > 0 else 0
            price_drop_html = f'<div class="price-drop">💰 Price dropped {abs(drop_pct):.1f}% from ${old:,.0f} to ${price:,.0f}</div>'

        return f"""
<div class="card {rec_class}">
  <span class="rec-badge" style="background:{rec_color}">{rec_emoji} {rec}</span>
  {price_drop_html}
  <div class="deal-address">{address}</div>
  <div class="deal-meta">{city}, TX · {units} units · Built {year} · ${price:,.0f}
  {"· " + source if source else ""}{"· " + dom_str if dom_str else ""}{occ_str}</div>

  <div class="metrics">
    <div class="metric">
      <div class="metric-value" style="color:#1a7f4b">{coc:.1f}%</div>
      <div class="metric-label">Value-Add CoC</div>
      <div class="metric-sub">(post-reno)</div>
    </div>
    <div class="metric">
      <div class="metric-value">{irr:.1f}%</div>
      <div class="metric-label">5-Yr IRR</div>
      <div class="metric-sub">{eq_mult:.2f}x equity mult</div>
    </div>
    <div class="metric">
      <div class="metric-value">{cap:.1f}%</div>
      <div class="metric-label">Cap Rate</div>
      <div class="metric-sub">DSCR: {dscr:.2f}</div>
    </div>
    <div class="metric">
      <div class="metric-value">${ppu:,.0f}</div>
      <div class="metric-label">Price / Unit</div>
    </div>
    <div class="metric">
      <div class="metric-value">${noi:,.0f}</div>
      <div class="metric-label">NOI / Year</div>
    </div>
    <div class="metric">
      <div class="metric-value">${exit_val/1e6:.2f}M</div>
      <div class="metric-label">Exit Value</div>
      <div class="metric-sub">(5yr @ 6.5% cap)</div>
    </div>
  </div>

  {f'<div class="one-liner">"{one_line}"</div>' if one_line else ""}
  {f'<div class="section-title">Deal Summary</div><div class="summary">{summary[:600]}</div>' if summary else ""}

  {"<div class='section-title'>Risks</div><ul class='flag-list red'>" + risks_html + "</ul>" if risks_html else ""}
  {"<div class='section-title'>Value-Add Opportunities</div><ul class='flag-list green'>" + opps_html + "</ul>" if opps_html else ""}
  {"<div class='section-title'>Due Diligence Priorities</div><ul class='flag-list'>" + dd_html + "</ul>" if dd_html else ""}

  <a href="{url}" class="cta-btn">View Listing →</a>
</div>"""

    def _stats_bar_html(self, stats: dict) -> str:
        return f"""
<div class="stats-bar">
  📊 <strong>Run Stats:</strong>
  {stats.get('deals_found', 0)} listings found ·
  {stats.get('deals_analyzed', 0)} analyzed ·
  {stats.get('deals_qualified', 0)} qualified ·
  Sources: {', '.join(stats.get('sources_scraped', []))}
</div>"""

    def _build_summary_html(self, stats: dict, top_deals: list[dict]) -> str:
        cards = "\n".join(self._deal_card_html(d) for d in top_deals[:5])
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body {{ font-family: -apple-system, sans-serif; background: #f5f5f5; padding: 20px; }}
.container {{ max-width: 680px; margin: 0 auto; }}
.header {{ background: #1a1a2e; color: white; padding: 24px; border-radius: 12px; margin-bottom: 16px; }}
.stat {{ display: inline-block; margin-right: 24px; }}
.stat-value {{ font-size: 28px; font-weight: 800; }}
.stat-label {{ font-size: 12px; opacity: 0.7; }}
</style></head>
<body><div class="container">
<div class="header">
  <h2 style="margin:0 0 16px">📊 Daily DFW Deal Summary — {datetime.now().strftime('%b %d, %Y')}</h2>
  <div class="stat"><div class="stat-value">{stats.get('total_tracked', 0)}</div><div class="stat-label">Total Tracked</div></div>
  <div class="stat"><div class="stat-value">{stats.get('qualified_deals', 0)}</div><div class="stat-label">Qualifying Deals</div></div>
  <div class="stat"><div class="stat-value">{stats.get('total_alerted', 0)}</div><div class="stat-label">Alerted All-Time</div></div>
</div>
<h3>Top Qualifying Deals</h3>
{cards if cards else "<p>No qualifying deals at this time.</p>"}
</div></body></html>"""

    def _build_text(self, deals: list[dict]) -> str:
        lines = [f"DFW Deal Alert — {len(deals)} deal(s)\n{'='*50}\n"]
        for d in deals:
            lines.append(f"""
{d.get('ai_recommendation','?')}: {d.get('address','?')}, {d.get('city','?')}, TX
Units: {d.get('units','?')} | Price: ${d.get('price',0):,.0f} | Built: {d.get('year_built','?')}
Value-Add CoC: {d.get('calc_va_coc',0):.1f}% | Cap Rate: {d.get('calc_cap_rate',0):.1f}% | 5yr IRR: {d.get('calc_irr_5yr',0):.1f}%
{d.get('ai_one_line','')}
URL: {d.get('url','?')}
{'─'*50}""")
        return "\n".join(lines)

    def _send(self, subject: str, html: str, text: str) -> bool:
        """Send via Gmail SMTP or SendGrid."""
        sender = self.config.get("sender")
        recipient = self.config.get("recipient")

        if not sender or not self.config.get("gmail_app_password"):
            logger.warning("Email not configured. Set GMAIL_SENDER and GMAIL_APP_PASSWORD in .env")
            logger.info(f"Would have sent: {subject}")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"DFW Deal Agent <{sender}>"
        msg["To"] = recipient
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(self.config["smtp_host"], self.config["smtp_port"]) as server:
                server.ehlo()
                server.starttls()
                server.login(sender, self.config["gmail_app_password"])
                server.sendmail(sender, recipient, msg.as_string())
            logger.info(f"Email sent: {subject}")
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("Gmail auth failed. Check GMAIL_SENDER and GMAIL_APP_PASSWORD in .env")
            return False
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False
