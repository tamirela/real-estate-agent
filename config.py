"""
Real Estate Deal Agent - Configuration
All your investment criteria live here. Edit this file to change parameters.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# SEARCH CRITERIA
# ─────────────────────────────────────────────
SEARCH_CRITERIA = {
    "markets": [
        "Dallas, TX",
        "Fort Worth, TX",
        "Plano, TX",
        "Irving, TX",
        "Arlington, TX",
        "Garland, TX",
        "Frisco, TX",
        "McKinney, TX",
        "Grand Prairie, TX",
        "Mesquite, TX",
        "Denton, TX",
        "Carrollton, TX",
        "Richardson, TX",
    ],
    "state": "TX",
    "metro": "Dallas-Fort Worth",
    "property_type": "multifamily",
    "asset_classes": ["B", "C"],       # Value-add focus
    "strategy": "value_add",
    "min_units": 10,
    "max_price": 8_000_000,
    "min_price": 1_000_000,            # Ignore sub-$1M (too small for 30+ units)
    "min_year_built": 1970,            # Class B/C era
    "max_year_built": 2005,
}

# ─────────────────────────────────────────────
# FINANCIAL HURDLES (your deal criteria)
# ─────────────────────────────────────────────
FINANCIAL_CRITERIA = {
    # Minimum return thresholds to trigger an alert
    "min_cash_on_cash": 0.20,          # 20% CoC minimum
    "min_cap_rate": 0.07,              # 7% cap rate minimum
    "min_irr_5yr": 0.18,               # 18% IRR over 5-year hold
    "min_equity_multiple": 1.8,        # 1.8x equity multiple (5-year)
    "max_price_per_unit": 120_000,     # $120k/unit max (DFW value-add)

    # Your loan terms
    "down_payment_pct": 0.20,          # 20% down
    "interest_rate": 0.075,            # 7.5% interest rate
    "loan_term_years": 30,
    "amortization_years": 30,

    # Operating assumptions (DFW market standards)
    "vacancy_rate": 0.07,              # 7% vacancy (DFW avg)
    "management_fee_pct": 0.08,        # 8% of EGI
    "maintenance_per_unit_yr": 1_200,  # $1,200/unit/year
    "insurance_per_unit_yr": 600,      # $600/unit/year
    "taxes_as_pct_of_value": 0.021,    # 2.1% (Dallas County average)
    "capex_reserve_per_unit_yr": 600,  # $600/unit/year CapEx reserve
    "admin_per_unit_yr": 300,          # $300/unit/year admin

    # Value-add assumptions (post-renovation stabilized)
    "value_add_rent_bump_pct": 0.20,   # Assume 20% rent bump after reno
    "reno_cost_per_unit": 8_000,       # $8k/unit average renovation cost
    "stabilization_months": 18,        # 18 months to stabilize

    # Exit assumptions (5-year hold)
    "hold_years": 5,
    "exit_cap_rate": 0.065,            # Conservative exit cap (compression)
    "selling_costs_pct": 0.04,         # 4% selling costs (broker, etc.)
}

# ─────────────────────────────────────────────
# DATA SOURCES & API KEYS
# ─────────────────────────────────────────────
API_KEYS = {
    "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
    "rentcast": os.getenv("RENTCAST_API_KEY", ""),          # rentcast.io - free tier
    "rapidapi": os.getenv("RAPIDAPI_KEY", ""),              # RapidAPI for Zillow
    "sendgrid": os.getenv("SENDGRID_API_KEY", ""),          # Email (optional)
}

# Gmail SMTP (simpler alternative to SendGrid)
EMAIL_CONFIG = {
    "recipient": "tamirelazr@gmail.com",
    "sender": os.getenv("GMAIL_SENDER", ""),                # your Gmail address
    "gmail_app_password": os.getenv("GMAIL_APP_PASSWORD", ""),  # Gmail app password
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "send_empty_run_summary": True,     # Always email so you know bot is alive
    "alert_on_price_drop": True,        # Alert when tracked deal drops in price
    "alert_on_new_deal": True,
}

# ─────────────────────────────────────────────
# TRACKING BEHAVIOR
# ─────────────────────────────────────────────
TRACKING = {
    "db_path": "deals.db",
    "check_interval_hours": 24,
    "price_drop_threshold_pct": 0.03,   # Alert if price drops 3%+
    "dom_alert_days": [30, 60, 90],     # Alert when deal has been on market X days
    "max_tracked_deals": 500,
    "stale_days": 180,                   # Remove deals not seen for 180 days
}

# ─────────────────────────────────────────────
# SCRAPER SETTINGS
# ─────────────────────────────────────────────
SCRAPER = {
    "request_delay_seconds": 2.0,       # Be polite, don't hammer servers
    "max_retries": 3,
    "timeout_seconds": 30,
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "sources": ["crexi", "loopnet", "marcus_millichap"],
}

# ─────────────────────────────────────────────
# BUILDOUT API TOKENS (Greysteel, Newmark, etc.)
# ─────────────────────────────────────────────
BUILDOUT_TOKENS = {
    "greysteel": {
        "token": "a6dbbaba3cc0ba7d1fbc587e9f06c953cebed964",
        "domain": "greysteel.com",
    },
    # "newmark": token broken (500 errors) — re-add when fixed
    "svn": {
        "token": "b933480474026c41d248b77156c84aef37dcac68",
        "domain": "svn.com",
    },
    "lee_associates": {
        "token": "9a64a93980aeae8db347e72cdfa8ca61017acc9a",
        "domain": "lee-associates.com",
    },
}

# ─────────────────────────────────────────────
# GOOGLE DRIVE OUTPUT (SET Holdings)
# ─────────────────────────────────────────────
DRIVE_CONFIG = {
    "credentials_path": os.path.expanduser("~/.mcp-gdrive/credentials.json"),
    "oauth_keys_path": os.path.expanduser("~/.mcp-gdrive/gcp-oauth.keys.json"),
    "set_folder_id": "1wf2kscuobaaLU1PNdWT-DaCkxGPaprZ5",
    "property_analysis_folder_id": "1rZHwIGVlEJ0q6_Kyww3u2pigg06GwdQB",
}

CRM_SHEET_ID = "1L6A9H_HESZBgjMDOPAr7YAa7lWJi1ERiYjy-jDadFgA"
