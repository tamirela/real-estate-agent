# DFW Multifamily Deal Agent 🏢

Automated agent that scans Crexi, LoopNet, and Marcus & Millichap daily for
multifamily deals in Dallas-Fort Worth matching your investment criteria.

## Your Criteria
| Parameter | Value |
|---|---|
| Market | Dallas-Fort Worth Metro |
| Property Type | Multifamily, Class B/C (value-add) |
| Min Units | 30+ |
| Max Price | $8,000,000 |
| Min Return | 20%+ cash-on-cash (value-add) |
| Loan Terms | 20% down @ 7.5% |
| Strategy | Value-add (20% rent bump, $8k/unit reno) |
| Hold Period | 5 years |
| Alert Email | tamirelazr@gmail.com |

---

## Quick Start (5 minutes)

### 1. Install Python dependencies
```bash
cd ~/real-estate-agent
pip install -r requirements.txt
```

### 2. Set up your environment file
```bash
cp .env.example .env
# Then edit .env with your API keys
```

You need:
- **Anthropic API key** → [console.anthropic.com](https://console.anthropic.com)
- **Gmail + App Password** → [Google Account → Security → App Passwords](https://myaccount.google.com/apppasswords)

### 3. Test your email setup
```bash
python main.py --test-email
```
You should receive a test deal alert at tamirelazr@gmail.com.

### 4. Run the agent now
```bash
python main.py
```

### 5. Run automatically every day
**Option A — Keep running locally:**
```bash
python main.py --schedule
```

**Option B — GitHub Actions (free, runs in the cloud):**
1. Push this folder to a private GitHub repo
2. Go to repo → Settings → Secrets and Variables → Actions
3. Add these secrets:
   - `ANTHROPIC_API_KEY`
   - `GMAIL_SENDER`
   - `GMAIL_APP_PASSWORD`
4. The workflow runs automatically every day at 8 AM Central

---

## What You'll Receive

**Instant alert email** when a new deal qualifies your criteria:

```
🔥 STRONG BUY: 48-unit Dallas deal @ 1234 Oak St — 23.4% CoC

Metrics:
  Value-Add CoC:  23.4%     │  5-Yr IRR:   24.1%
  Cap Rate:        7.8%     │  DSCR:        1.18
  Price/Unit:    $89,500    │  Exit Value: $6.1M

"Classic 1988 Class C with rents 18% below market —
 strong value-add play if you can execute on the renovation."

Risks:
  ⚠️ Low occupancy (78%) — turnaround risk
  ⚠️ Older plumbing likely needs repiping

Opportunities:
  💡 Rents ~18% below DFW market average
  💡 Built 1988 — classic value-add vintage
  💡 Professional management could stabilize quickly

Due Diligence:
  📋 Verify T-12 income/expense statement
  📋 Get rent roll and current lease terms
  📋 Inspect HVAC units (original equipment?)

[View Listing →]
```

---

## How the Numbers Work

The agent calculates everything from scratch using your loan terms. It does NOT trust the seller's stated cap rate.

**Income:**
- Gross Potential Rent (estimated from market data or listing)
- Less 7% vacancy = Effective Gross Income

**Expenses:**
- Property taxes (2.1% of value — Dallas County)
- Insurance ($600/unit/yr)
- Management (8% of EGI)
- Maintenance ($1,200/unit/yr)
- CapEx Reserve ($600/unit/yr)
- Admin ($300/unit/yr)

**Returns:**
- Cap Rate = NOI / Price
- Cash-on-Cash = (NOI - Debt Service) / Down Payment
- Value-Add CoC = same but with 20% rent bump after $8k/unit reno
- 5-Yr IRR = discounted cash flows + exit at 6.5% cap rate

**Alert triggers:** Must meet at least 2 of:
- Value-add CoC ≥ 20%
- 5-yr IRR ≥ 18%
- Cap rate ≥ 7%
- Price/unit ≤ $120k

---

## Files Overview
```
real-estate-agent/
├── main.py                    # Run this
├── config.py                  # All your criteria — edit here
├── .env                       # Your API keys (create from .env.example)
├── deals.db                   # SQLite database (auto-created)
├── agent.log                  # Run logs
├── scrapers/
│   ├── crexi.py               # Crexi.com scraper
│   ├── loopnet.py             # LoopNet scraper
│   └── marcus_millichap.py    # Marcus & Millichap scraper
├── analyzers/
│   ├── financials.py          # All the math (CoC, IRR, cap rate)
│   └── claude_ai.py           # AI deal memo generator
├── database/
│   └── tracker.py             # SQLite — tracks deals & price history
├── alerts/
│   └── email_sender.py        # Beautiful HTML email alerts
└── .github/workflows/
    └── daily_scan.yml         # GitHub Actions (runs daily at 8 AM CT)
```

---

## Customizing Your Criteria

Edit `config.py`:

```python
FINANCIAL_CRITERIA = {
    "min_cash_on_cash": 0.20,      # Change 0.20 to 0.15 for 15% threshold
    "min_cap_rate": 0.07,          # Minimum cap rate
    "down_payment_pct": 0.20,      # Your down payment %
    "interest_rate": 0.075,        # Your loan rate
    "value_add_rent_bump_pct": 0.20,  # Assumed rent increase post-reno
    "reno_cost_per_unit": 8_000,   # Your renovation budget per unit
}

SEARCH_CRITERIA = {
    "min_units": 30,               # Minimum unit count
    "max_price": 8_000_000,        # Maximum purchase price
}
```

---

## Commands
```bash
python main.py                  # Run once
python main.py --schedule       # Run daily (keeps process alive)
python main.py --summary        # Email summary of all tracked deals
python main.py --test-email     # Test your email configuration
```
