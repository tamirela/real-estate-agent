"""
DFW Multifamily Deal Agent — Main Orchestrator
============================================================
Runs daily to find, analyze, and alert on qualifying deals.

Usage:
  python main.py              # Run once now
  python main.py --schedule   # Run on daily schedule (keep process alive)
  python main.py --summary    # Send daily summary email of all tracked deals
  python main.py --test-email # Send a test email to verify setup
"""

import time
import logging
import argparse
from datetime import datetime

from config import SEARCH_CRITERIA, FINANCIAL_CRITERIA, TRACKING, EMAIL_CONFIG
from scrapers import (
    CrexiBrowserScraper, LoopNetScraper, RentCastScraper, ZillowScraper, RedfinScraper,
    BuildoutScraper, MultifamilyGroupScraper, SilvaMultifamilyScraper, IpaTexasScraper,
)
from analyzers import FinancialAnalyzer, ClaudeAnalyzer, MarketCompAnalyzer
from database import DealTracker
from alerts import EmailAlerter
from outputs import DriveOutput, CrmSheet

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("agent.log"),
    ]
)
logger = logging.getLogger("main")


def run_agent() -> dict:
    """
    Single full run of the deal agent:
    1. Scrape listings from all sources
    2. Analyze each with financial engine
    3. Run Claude AI memo on qualifying deals
    4. Track all deals in SQLite
    5. Email alerts for new qualifying deals
    """
    start_time = time.time()
    logger.info("=" * 60)
    logger.info(f"DFW Deal Agent starting — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info("=" * 60)

    tracker = DealTracker()
    fin_analyzer = FinancialAnalyzer()
    market_comp = MarketCompAnalyzer()
    claude = ClaudeAnalyzer()
    emailer = EmailAlerter()
    drive_output = DriveOutput()
    crm_sheet = CrmSheet()
    markets = SEARCH_CRITERIA["markets"]

    stats = {
        "deals_found": 0,
        "deals_analyzed": 0,
        "deals_qualified": 0,
        "deals_alerted": 0,
        "sources_scraped": [],
        "errors": [],
    }

    # ── STEP 1: SCRAPE ─────────────────────────────────────────────────────────
    scrapers = [
        ("buildout", BuildoutScraper()),     # Greysteel, SVN, Lee — open JSON API (WORKING)
        # TODO: re-enable once fixed:
        # ("multifamily_group", MultifamilyGroupScraper()),   # 403 blocked
        # ("silva_multifamily", SilvaMultifamilyScraper()),   # 404 all pages
        # ("ipa_texas", IpaTexasScraper()),                   # no cards found
        # ("crexi", CrexiBrowserScraper()),     # login button not found
        # ("loopnet", LoopNetScraper()),        # Akamai blocks detail fetch — use email alerts instead
        # ("zillow", ZillowScraper()),          # API endpoint changed (404)
        # ("redfin", RedfinScraper()),          # 0 results
    ]

    all_listings = []
    for source_name, scraper in scrapers:
        try:
            logger.info(f"Scraping {source_name}...")
            listings = scraper.scrape(markets)
            all_listings.extend(listings)
            stats["sources_scraped"].append(source_name)
            logger.info(f"  → {len(listings)} listings from {source_name}")
        except Exception as e:
            logger.error(f"Scraper {source_name} failed: {e}")
            stats["errors"].append(f"{source_name}: {str(e)}")

    stats["deals_found"] = len(all_listings)
    logger.info(f"\nTotal listings scraped: {len(all_listings)}")

    # Deduplicate by URL
    seen_urls = set()
    unique_listings = []
    for l in all_listings:
        key = f"{l.source}:{l.external_id}"
        if key not in seen_urls:
            seen_urls.add(key)
            unique_listings.append(l)

    logger.info(f"Unique listings after dedup: {len(unique_listings)}")

    # ── STEP 2: FINANCIAL ANALYSIS ──────────────────────────────────────────────
    logger.info("\nRunning financial analysis...")
    deals_to_alert = []

    for i, listing in enumerate(unique_listings):
        try:
            # Skip price=0 listings — log them as "Contact Broker" leads
            if listing.price <= 0:
                logger.info(
                    f"  📋 CONTACT BROKER: {listing.address}, {listing.city} | "
                    f"{listing.units}u | No price listed | {listing.source}"
                )
                # Track in DB for dedup even without analysis
                tracker.upsert_deal(listing, None, None)
                continue

            metrics = fin_analyzer.analyze(listing)
            if not metrics:
                continue

            stats["deals_analyzed"] += 1

            # ── STEP 2b: RENT/SF MARKET COMPARISON (critical filter) ──────
            try:
                comp_result = market_comp.analyze(listing)
                if comp_result:
                    metrics.subject_rent_sf = comp_result.get("subject_rent_sf")
                    metrics.market_rent_sf = comp_result.get("market_rent_sf")
                    metrics.rent_sf_spread_pct = comp_result.get("spread_pct")
                    metrics.rent_sf_verdict = comp_result.get("verdict")

                    if comp_result["verdict"] == "NO-GO":
                        metrics.passes_hurdle = False
                        metrics.hurdle_reason = "❌ Rent/SF at or above market — no renovation spread"
                        metrics.red_flags.append(
                            f"Rent/SF ${comp_result['subject_rent_sf']:.2f} vs market ${comp_result['market_rent_sf']:.2f} — no upside"
                        )
                        logger.info(
                            f"  ❌ NO-GO (rent/SF): {listing.address} | "
                            f"${comp_result['subject_rent_sf']:.2f}/SF vs market ${comp_result['market_rent_sf']:.2f}/SF"
                        )
            except Exception as e:
                logger.warning(f"Market comp failed for {listing.external_id}: {e}")

            if metrics.passes_hurdle:
                stats["deals_qualified"] += 1
                logger.info(
                    f"  ✅ QUALIFIES: {listing.address}, {listing.city} | "
                    f"${listing.price:,.0f} | {listing.units}u | "
                    f"CoC: {metrics.va_cash_on_cash*100:.1f}% | "
                    f"IRR: {metrics.irr_5yr*100:.1f}%"
                )

                # ── STEP 3: AI ANALYSIS (qualifying deals only) ─────────────────
                ai_result = claude.analyze(listing, metrics)

                # ── STEP 3b: GENERATE DEAL FOLDER + FILES ──────────────────────
                folder_link = None
                try:
                    deal_data = _build_deal_data(listing, metrics, ai_result)
                    folder_id, folder_link = drive_output.create_deal_package(deal_data)
                    logger.info(f"  📁 Drive folder created: {folder_link}")
                except Exception as e:
                    logger.error(f"Drive output failed for {listing.external_id}: {e}")

                # ── STEP 3c: UPDATE CRM SHEET ──────────────────────────────────
                try:
                    crm_sheet.add_deal(listing, metrics, ai_result, folder_link)
                    logger.info(f"  📊 CRM updated for {listing.address}")
                except Exception as e:
                    logger.error(f"CRM update failed for {listing.external_id}: {e}")

                # ── STEP 4: TRACK IN DB ─────────────────────────────────────────
                is_new, price_dropped, should_alert = tracker.upsert_deal(
                    listing, metrics, ai_result
                )

                if should_alert:
                    deal_dict = _build_alert_dict(listing, metrics, ai_result, price_dropped)
                    deal_dict["drive_folder_link"] = folder_link
                    deals_to_alert.append((deal_dict, price_dropped))
                    tracker.mark_alerted(listing.source, listing.external_id)
                    stats["deals_alerted"] += 1
            else:
                # Still track non-qualifying deals (for price drop monitoring)
                tracker.upsert_deal(listing, metrics, None)

        except Exception as e:
            logger.error(f"Error processing {listing.external_id}: {e}")
            stats["errors"].append(f"analysis:{listing.external_id}: {str(e)}")

    # ── STEP 5: SEND EMAIL ALERTS ───────────────────────────────────────────────
    if deals_to_alert:
        # Group by type (new vs price drop)
        new_deals = [d for d, pd in deals_to_alert if not pd]
        price_drop_deals = [d for d, pd in deals_to_alert if pd]

        if new_deals:
            logger.info(f"\nSending alert for {len(new_deals)} new qualifying deal(s)...")
            emailer.send_deal_alert(new_deals, is_price_drop=False, run_stats=stats)

        if price_drop_deals:
            logger.info(f"Sending price drop alert for {len(price_drop_deals)} deal(s)...")
            emailer.send_deal_alert(price_drop_deals, is_price_drop=True)
    else:
        logger.info("\nNo new qualifying deals to alert on.")

    # ── STEP 6: MARK STALE DEALS ────────────────────────────────────────────────
    tracker.mark_stale()

    duration = time.time() - start_time
    stats["duration_seconds"] = duration

    # Log run to DB
    tracker.log_run(**stats)

    logger.info(f"\n{'='*60}")
    logger.info(f"Run complete in {duration:.1f}s")
    logger.info(f"  Found:     {stats['deals_found']} listings")
    logger.info(f"  Analyzed:  {stats['deals_analyzed']} deals")
    logger.info(f"  Qualified: {stats['deals_qualified']} deals")
    logger.info(f"  Alerted:   {stats['deals_alerted']} alerts sent")
    logger.info(f"  Errors:    {len(stats['errors'])}")
    logger.info(f"{'='*60}\n")

    return stats


def _build_deal_data(listing, metrics, ai_result) -> dict:
    """Build deal data dict for Drive output templates."""
    ai = ai_result or {}
    return {
        "property_name": listing.address,
        "address": listing.address,
        "city": listing.city,
        "state": listing.state,
        "zip_code": listing.zip_code,
        "units": listing.units,
        "sqft": listing.sqft or (listing.units * 750),
        "year_built": listing.year_built,
        "price": listing.price,
        "price_per_unit": metrics.price_per_unit,
        "noi": metrics.noi,
        "cap_rate": metrics.cap_rate,
        "egi": metrics.effective_gross_income,
        "total_opex": metrics.total_operating_expenses,
        "gross_rent_annual": metrics.gross_potential_rent_annual,
        "va_noi": metrics.va_noi,
        "va_cap_rate": metrics.va_cap_rate,
        "irr_5yr": metrics.irr_5yr,
        "equity_multiple_5yr": metrics.equity_multiple_5yr,
        "exit_value": metrics.exit_value,
        "subject_rent_sf": metrics.subject_rent_sf,
        "market_rent_sf": metrics.market_rent_sf,
        "rent_sf_spread_pct": metrics.rent_sf_spread_pct,
        "rent_sf_verdict": metrics.rent_sf_verdict,
        "red_flags": metrics.red_flags,
        "value_add_signals": metrics.value_add_signals,
        "passes_hurdle": metrics.passes_hurdle,
        "hurdle_reason": metrics.hurdle_reason,
        "source": listing.source,
        "url": listing.url,
        "ai_recommendation": ai.get("recommendation", ""),
        "ai_summary": ai.get("summary", ""),
    }


def _build_alert_dict(listing, metrics, ai_result, price_dropped: bool) -> dict:
    """Merge listing + metrics + AI result into a flat dict for emailing."""
    m = metrics.to_dict()
    ai = ai_result or {}
    return {
        # Listing data
        "source": listing.source,
        "external_id": listing.external_id,
        "url": listing.url,
        "address": listing.address,
        "city": listing.city,
        "state": listing.state,
        "zip_code": listing.zip_code,
        "price": listing.price,
        "units": listing.units,
        "year_built": listing.year_built,
        "property_class": listing.property_class,
        "occupancy_rate": listing.occupancy_rate,
        "days_on_market": listing.days_on_market,
        # Metrics
        "price_per_unit": m.get("price_per_unit"),
        "calc_cap_rate": m.get("cap_rate"),
        "calc_coc": m.get("cash_on_cash"),
        "calc_va_coc": m.get("va_cash_on_cash"),
        "calc_irr_5yr": m.get("irr_5yr"),
        "calc_equity_multiple": m.get("equity_multiple_5yr"),
        "calc_noi": m.get("noi"),
        "calc_dscr": m.get("dscr"),
        "calc_grm": m.get("grm"),
        "calc_exit_value": m.get("exit_value"),
        "red_flags": m.get("red_flags", []),
        "value_add_signals": m.get("value_add_signals", []),
        # AI
        "ai_recommendation": ai.get("recommendation", "WATCH"),
        "ai_one_line": ai.get("one_line", ""),
        "ai_summary": ai.get("summary", ""),
        "ai_risks": ai.get("top_risks", []),
        "ai_opportunities": ai.get("top_opportunities", []),
        "ai_due_diligence": ai.get("due_diligence", []),
        # Meta
        "_price_dropped": price_dropped,
    }


def send_daily_summary():
    """Email a daily summary of all active qualifying deals."""
    tracker = DealTracker()
    emailer = EmailAlerter()
    stats = tracker.get_stats()
    top_deals = tracker.get_all_active_qualifying()
    emailer.send_daily_summary(stats, top_deals[:10])
    logger.info(f"Daily summary sent. {len(top_deals)} qualifying deals in database.")


def send_test_email():
    """Send a test email to verify Gmail setup."""
    from alerts.email_sender import EmailAlerter
    emailer = EmailAlerter()
    test_deal = {
        "source": "test",
        "external_id": "TEST001",
        "url": "https://www.crexi.com",
        "address": "1234 Test Property Dr",
        "city": "Dallas",
        "state": "TX",
        "zip_code": "75201",
        "price": 4_500_000,
        "units": 48,
        "year_built": 1988,
        "property_class": "C",
        "occupancy_rate": 0.78,
        "days_on_market": 22,
        "price_per_unit": 93_750,
        "calc_cap_rate": 7.8,
        "calc_coc": 11.2,
        "calc_va_coc": 21.4,
        "calc_irr_5yr": 22.1,
        "calc_equity_multiple": 2.3,
        "calc_noi": 351_000,
        "calc_dscr": 1.18,
        "calc_grm": 9.2,
        "calc_exit_value": 5_861_538,
        "red_flags": ["Low occupancy (78%) — turnaround risk"],
        "value_add_signals": ["Built 1988 - classic value-add vintage", "Below-market rents"],
        "ai_recommendation": "BUY",
        "ai_one_line": "48-unit 1988 Class C in Dallas at $93,750/door — strong value-add upside if you can push rents 20%.",
        "ai_summary": "This is a test email to verify your DFW Deal Agent is configured correctly.",
        "ai_risks": ["Low occupancy (78%)", "Older vintage plumbing/electrical"],
        "ai_opportunities": ["Rents ~15% below market", "Professional management could stabilize quickly"],
        "ai_due_diligence": ["Verify T-12 actuals", "Check utility costs", "Inspect HVAC"],
        "_price_dropped": False,
    }
    success = emailer.send_deal_alert([test_deal])
    if success:
        print("✅ Test email sent successfully to tamirelazr@gmail.com")
    else:
        print("❌ Email failed. Check GMAIL_SENDER and GMAIL_APP_PASSWORD in your .env file")


def run_on_schedule():
    """Keep process alive and run once per day."""
    import schedule
    schedule.every().day.at("07:00").do(run_agent)
    schedule.every().day.at("07:30").do(send_daily_summary)
    logger.info("Scheduled to run daily at 7:00 AM. Keeping process alive...")
    logger.info("Running first scan now...")
    run_agent()
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DFW Multifamily Deal Agent")
    parser.add_argument("--schedule", action="store_true", help="Run on daily schedule")
    parser.add_argument("--summary", action="store_true", help="Send daily summary email")
    parser.add_argument("--test-email", action="store_true", help="Send test email")
    parser.add_argument("--buildout-only", action="store_true", help="Test Buildout scraper only")
    args = parser.parse_args()

    if args.buildout_only:
        logger.info("Testing Buildout scraper only...")
        scraper = BuildoutScraper()
        listings = scraper.scrape(SEARCH_CRITERIA["markets"])
        for l in listings:
            logger.info(f"  {l.address}, {l.city} | {l.units}u | ${l.price:,.0f} | {l.source}")
        logger.info(f"Total: {len(listings)} listings from Buildout")
    elif args.schedule:
        run_on_schedule()
    elif args.summary:
        send_daily_summary()
    elif args.test_email:
        send_test_email()
    else:
        run_agent()
