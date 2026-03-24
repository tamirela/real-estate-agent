"""
SQLite deal tracker.
Stores all deals found, tracks price changes over time, and prevents duplicate alerts.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager
from config import TRACKING

logger = logging.getLogger(__name__)


class DealTracker:
    """Persists deals to SQLite and tracks changes over time."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or TRACKING["db_path"]
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    url TEXT,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    units INTEGER,
                    year_built INTEGER,
                    property_class TEXT,

                    -- Current snapshot
                    price REAL,
                    price_per_unit REAL,
                    cap_rate_listed REAL,
                    occupancy_rate REAL,
                    days_on_market INTEGER,
                    gross_monthly_rent REAL,
                    annual_noi REAL,

                    -- Calculated metrics
                    calc_cap_rate REAL,
                    calc_coc REAL,
                    calc_va_coc REAL,
                    calc_irr_5yr REAL,
                    calc_equity_multiple REAL,
                    calc_noi REAL,
                    calc_dscr REAL,
                    calc_grm REAL,
                    calc_exit_value REAL,

                    -- AI analysis
                    ai_recommendation TEXT,
                    ai_one_line TEXT,
                    ai_summary TEXT,
                    ai_risks TEXT,          -- JSON array
                    ai_opportunities TEXT,  -- JSON array
                    ai_due_diligence TEXT,  -- JSON array
                    ai_full_memo TEXT,

                    -- Flags
                    red_flags TEXT,         -- JSON array
                    value_add_signals TEXT, -- JSON array
                    passes_hurdle INTEGER DEFAULT 0,
                    hurdle_reason TEXT,

                    -- Tracking
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    last_alerted TEXT,
                    alert_count INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,

                    UNIQUE(source, external_id)
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deal_id INTEGER NOT NULL,
                    price REAL NOT NULL,
                    recorded_at TEXT NOT NULL,
                    change_pct REAL,
                    FOREIGN KEY(deal_id) REFERENCES deals(id)
                );

                CREATE TABLE IF NOT EXISTS run_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    deals_found INTEGER DEFAULT 0,
                    deals_analyzed INTEGER DEFAULT 0,
                    deals_qualified INTEGER DEFAULT 0,
                    deals_alerted INTEGER DEFAULT 0,
                    sources_scraped TEXT,
                    errors TEXT,
                    duration_seconds REAL
                );

                CREATE INDEX IF NOT EXISTS idx_deals_source_id ON deals(source, external_id);
                CREATE INDEX IF NOT EXISTS idx_deals_passes ON deals(passes_hurdle, is_active);
                CREATE INDEX IF NOT EXISTS idx_deals_city ON deals(city);
                CREATE INDEX IF NOT EXISTS idx_price_history_deal ON price_history(deal_id);
            """)
        logger.info(f"Database initialized: {self.db_path}")

    def upsert_deal(
        self,
        listing,
        metrics,
        ai_result: Optional[dict] = None,
    ) -> tuple[bool, bool, bool]:
        """
        Insert or update a deal. Returns (is_new, price_dropped, should_alert).
        """
        now = datetime.utcnow().isoformat()
        m = metrics.to_dict() if metrics else {}
        ai = ai_result or {}

        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM deals WHERE source=? AND external_id=?",
                (listing.source, listing.external_id)
            ).fetchone()

            if existing:
                # Check for price drop
                old_price = existing["price"]
                price_change_pct = (listing.price - old_price) / old_price if old_price > 0 else 0
                price_dropped = price_change_pct <= -TRACKING["price_drop_threshold_pct"]

                # Update existing record
                conn.execute("""
                    UPDATE deals SET
                        price=?, price_per_unit=?, cap_rate_listed=?, occupancy_rate=?,
                        days_on_market=?, gross_monthly_rent=?, annual_noi=?,
                        calc_cap_rate=?, calc_coc=?, calc_va_coc=?, calc_irr_5yr=?,
                        calc_equity_multiple=?, calc_noi=?, calc_dscr=?, calc_grm=?,
                        calc_exit_value=?,
                        ai_recommendation=?, ai_one_line=?, ai_summary=?,
                        ai_risks=?, ai_opportunities=?, ai_due_diligence=?, ai_full_memo=?,
                        red_flags=?, value_add_signals=?,
                        passes_hurdle=?, hurdle_reason=?,
                        last_seen=?, is_active=1
                    WHERE source=? AND external_id=?
                """, (
                    listing.price, m.get("price_per_unit"), listing.cap_rate_listed,
                    listing.occupancy_rate, listing.days_on_market,
                    listing.gross_monthly_rent, listing.annual_noi,
                    m.get("cap_rate"), m.get("cash_on_cash"), m.get("va_cash_on_cash"),
                    m.get("irr_5yr"), m.get("equity_multiple_5yr"), m.get("noi"),
                    m.get("dscr"), m.get("grm"), m.get("exit_value"),
                    ai.get("recommendation"), ai.get("one_line"), ai.get("summary"),
                    json.dumps(ai.get("top_risks", [])),
                    json.dumps(ai.get("top_opportunities", [])),
                    json.dumps(ai.get("due_diligence", [])),
                    ai.get("full_memo"),
                    json.dumps(m.get("red_flags", [])),
                    json.dumps(m.get("value_add_signals", [])),
                    1 if m.get("passes_hurdle") else 0,
                    m.get("hurdle_reason"),
                    now,
                    listing.source, listing.external_id,
                ))

                # Record price history if price changed
                if price_dropped or abs(price_change_pct) > 0.001:
                    conn.execute(
                        "INSERT INTO price_history (deal_id, price, recorded_at, change_pct) VALUES (?,?,?,?)",
                        (existing["id"], listing.price, now, price_change_pct)
                    )

                # Should we re-alert? (new qualification or price drop)
                last_alerted = existing["last_alerted"]
                was_alerted = last_alerted is not None
                should_alert = (
                    m.get("passes_hurdle") and
                    (not was_alerted or price_dropped)
                )

                return False, price_dropped, should_alert

            else:
                # New deal
                conn.execute("""
                    INSERT INTO deals (
                        source, external_id, url, address, city, state, zip_code,
                        units, year_built, property_class,
                        price, price_per_unit, cap_rate_listed, occupancy_rate,
                        days_on_market, gross_monthly_rent, annual_noi,
                        calc_cap_rate, calc_coc, calc_va_coc, calc_irr_5yr,
                        calc_equity_multiple, calc_noi, calc_dscr, calc_grm, calc_exit_value,
                        ai_recommendation, ai_one_line, ai_summary,
                        ai_risks, ai_opportunities, ai_due_diligence, ai_full_memo,
                        red_flags, value_add_signals,
                        passes_hurdle, hurdle_reason,
                        first_seen, last_seen
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    listing.source, listing.external_id, listing.url,
                    listing.address, listing.city, listing.state, listing.zip_code,
                    listing.units, listing.year_built, listing.property_class,
                    listing.price, m.get("price_per_unit"), listing.cap_rate_listed,
                    listing.occupancy_rate, listing.days_on_market,
                    listing.gross_monthly_rent, listing.annual_noi,
                    m.get("cap_rate"), m.get("cash_on_cash"), m.get("va_cash_on_cash"),
                    m.get("irr_5yr"), m.get("equity_multiple_5yr"), m.get("noi"),
                    m.get("dscr"), m.get("grm"), m.get("exit_value"),
                    ai.get("recommendation"), ai.get("one_line"), ai.get("summary"),
                    json.dumps(ai.get("top_risks", [])),
                    json.dumps(ai.get("top_opportunities", [])),
                    json.dumps(ai.get("due_diligence", [])),
                    ai.get("full_memo"),
                    json.dumps(m.get("red_flags", [])),
                    json.dumps(m.get("value_add_signals", [])),
                    1 if m.get("passes_hurdle") else 0,
                    m.get("hurdle_reason"),
                    now, now,
                ))
                # Initial price history entry
                deal_id = conn.execute(
                    "SELECT id FROM deals WHERE source=? AND external_id=?",
                    (listing.source, listing.external_id)
                ).fetchone()["id"]
                conn.execute(
                    "INSERT INTO price_history (deal_id, price, recorded_at, change_pct) VALUES (?,?,?,0)",
                    (deal_id, listing.price, now)
                )

                return True, False, bool(m.get("passes_hurdle"))

    def mark_alerted(self, source: str, external_id: str):
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE deals SET last_alerted=?, alert_count=alert_count+1 WHERE source=? AND external_id=?",
                (now, source, external_id)
            )

    def mark_stale(self):
        """Mark deals not seen recently as inactive."""
        cutoff = (datetime.utcnow() - timedelta(days=TRACKING["stale_days"])).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE deals SET is_active=0 WHERE last_seen < ? AND is_active=1",
                (cutoff,)
            )

    def get_all_active_qualifying(self) -> list[dict]:
        """Get all active deals that pass hurdles (for dashboard/summary)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM deals
                WHERE passes_hurdle=1 AND is_active=1
                ORDER BY calc_va_coc DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_price_history(self, source: str, external_id: str) -> list[dict]:
        with self._conn() as conn:
            deal = conn.execute(
                "SELECT id FROM deals WHERE source=? AND external_id=?",
                (source, external_id)
            ).fetchone()
            if not deal:
                return []
            rows = conn.execute(
                "SELECT * FROM price_history WHERE deal_id=? ORDER BY recorded_at",
                (deal["id"],)
            ).fetchall()
            return [dict(r) for r in rows]

    def log_run(self, **kwargs):
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO run_log (run_at, deals_found, deals_analyzed, deals_qualified,
                    deals_alerted, sources_scraped, errors, duration_seconds)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                now,
                kwargs.get("deals_found", 0),
                kwargs.get("deals_analyzed", 0),
                kwargs.get("deals_qualified", 0),
                kwargs.get("deals_alerted", 0),
                json.dumps(kwargs.get("sources_scraped", [])),
                json.dumps(kwargs.get("errors", [])),
                kwargs.get("duration_seconds", 0),
            ))

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM deals WHERE is_active=1").fetchone()[0]
            qualified = conn.execute("SELECT COUNT(*) FROM deals WHERE passes_hurdle=1 AND is_active=1").fetchone()[0]
            alerted = conn.execute("SELECT COUNT(*) FROM deals WHERE last_alerted IS NOT NULL").fetchone()[0]
            last_run = conn.execute("SELECT run_at FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
            return {
                "total_tracked": total,
                "qualified_deals": qualified,
                "total_alerted": alerted,
                "last_run": last_run[0] if last_run else "Never",
            }
