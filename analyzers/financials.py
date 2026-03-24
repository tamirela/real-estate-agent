"""
Financial analysis engine for multifamily deals.
Calculates: Cap Rate, Cash-on-Cash, NOI, DSCR, IRR, Equity Multiple, GRM.

All math is based on your investment parameters in config.py.
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Optional
from scrapers.base import RawListing
from config import FINANCIAL_CRITERIA

logger = logging.getLogger(__name__)


@dataclass
class DealMetrics:
    """All financial metrics for a deal."""
    # Inputs
    purchase_price: float
    units: int
    down_payment: float
    loan_amount: float
    annual_loan_payment: float

    # Income
    gross_potential_rent_annual: float
    effective_gross_income: float  # after vacancy

    # Expenses
    total_operating_expenses: float
    noi: float                          # Net Operating Income

    # Returns (as-is)
    cap_rate: float
    cash_on_cash: float
    annual_cash_flow: float
    dscr: float                         # Debt Service Coverage Ratio
    grm: float                          # Gross Rent Multiplier
    price_per_unit: float

    # Value-add projections (post-renovation)
    va_noi: float                       # Stabilized NOI after reno
    va_cash_on_cash: float              # CoC after reno + reno costs
    va_cap_rate: float

    # 5-year hold analysis
    irr_5yr: float
    equity_multiple_5yr: float
    total_profit_5yr: float
    exit_value: float

    # Rent/SF Market Comparison
    subject_rent_sf: Optional[float] = None
    market_rent_sf: Optional[float] = None
    rent_sf_spread_pct: Optional[float] = None
    rent_sf_verdict: Optional[str] = None

    # Flags
    passes_hurdle: bool = False
    hurdle_reason: str = ""             # What criterion it met or failed
    red_flags: list[str] = field(default_factory=list)
    value_add_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "purchase_price": self.purchase_price,
            "units": self.units,
            "down_payment": self.down_payment,
            "loan_amount": self.loan_amount,
            "gross_potential_rent_annual": self.gross_potential_rent_annual,
            "effective_gross_income": self.effective_gross_income,
            "total_operating_expenses": self.total_operating_expenses,
            "noi": self.noi,
            "cap_rate": round(self.cap_rate * 100, 2),
            "cash_on_cash": round(self.cash_on_cash * 100, 2),
            "annual_cash_flow": round(self.annual_cash_flow, 0),
            "dscr": round(self.dscr, 2),
            "grm": round(self.grm, 1),
            "price_per_unit": round(self.price_per_unit, 0),
            "va_noi": round(self.va_noi, 0),
            "va_cash_on_cash": round(self.va_cash_on_cash * 100, 2),
            "va_cap_rate": round(self.va_cap_rate * 100, 2),
            "irr_5yr": round(self.irr_5yr * 100, 2),
            "equity_multiple_5yr": round(self.equity_multiple_5yr, 2),
            "total_profit_5yr": round(self.total_profit_5yr, 0),
            "exit_value": round(self.exit_value, 0),
            "subject_rent_sf": round(self.subject_rent_sf, 2) if self.subject_rent_sf is not None else None,
            "market_rent_sf": round(self.market_rent_sf, 2) if self.market_rent_sf is not None else None,
            "rent_sf_spread_pct": round(self.rent_sf_spread_pct * 100, 2) if self.rent_sf_spread_pct is not None else None,
            "rent_sf_verdict": self.rent_sf_verdict,
            "passes_hurdle": self.passes_hurdle,
            "hurdle_reason": self.hurdle_reason,
            "red_flags": self.red_flags,
            "value_add_signals": self.value_add_signals,
        }


class FinancialAnalyzer:
    """
    Analyzes a multifamily listing and calculates all key investment metrics.
    Uses config.py parameters for loan terms and market assumptions.
    """

    def __init__(self):
        self.c = FINANCIAL_CRITERIA

    def analyze(self, listing: RawListing) -> Optional[DealMetrics]:
        """
        Main entry point. Returns DealMetrics or None if insufficient data.
        Falls back to market estimates when listing data is incomplete.
        """
        try:
            price = listing.price
            units = listing.units

            if price <= 0 or units <= 0:
                return None

            # ── INCOME ─────────────────────────────────────────────
            gross_monthly_rent = self._estimate_gross_rent(listing)
            gross_annual_rent = gross_monthly_rent * 12
            vacancy_loss = gross_annual_rent * self.c["vacancy_rate"]
            egi = gross_annual_rent - vacancy_loss  # Effective Gross Income

            # ── EXPENSES ───────────────────────────────────────────
            taxes = price * self.c["taxes_as_pct_of_value"]
            insurance = units * self.c["insurance_per_unit_yr"]
            management = egi * self.c["management_fee_pct"]
            maintenance = units * self.c["maintenance_per_unit_yr"]
            capex = units * self.c["capex_reserve_per_unit_yr"]
            admin = units * self.c["admin_per_unit_yr"]
            total_opex = taxes + insurance + management + maintenance + capex + admin

            # ── NOI ────────────────────────────────────────────────
            noi = egi - total_opex

            # ── LOAN ───────────────────────────────────────────────
            down_payment = price * self.c["down_payment_pct"]
            loan_amount = price - down_payment
            annual_debt_service = self._calculate_annual_debt_service(loan_amount)

            # ── RETURNS (AS-IS) ────────────────────────────────────
            cap_rate = noi / price if price > 0 else 0
            annual_cash_flow = noi - annual_debt_service
            cash_on_cash = annual_cash_flow / down_payment if down_payment > 0 else 0
            dscr = noi / annual_debt_service if annual_debt_service > 0 else 0
            grm = price / gross_annual_rent if gross_annual_rent > 0 else 0
            price_per_unit = price / units

            # ── VALUE-ADD PROJECTIONS ──────────────────────────────
            va_metrics = self._calculate_value_add(
                price, units, noi, down_payment, annual_debt_service, listing
            )

            # ── 5-YEAR HOLD ────────────────────────────────────────
            irr, equity_mult, total_profit, exit_value = self._calculate_5yr_hold(
                price, down_payment, loan_amount,
                va_metrics["va_noi"], annual_debt_service
            )

            # ── HURDLE CHECK ───────────────────────────────────────
            passes, reason = self._check_hurdles(
                cash_on_cash, va_metrics["va_cash_on_cash"], cap_rate, irr, equity_mult, price_per_unit
            )

            # ── FLAGS ──────────────────────────────────────────────
            red_flags = self._identify_red_flags(
                listing, cap_rate, dscr, price_per_unit, gross_monthly_rent
            )
            value_add_signals = self._identify_value_add_signals(listing)

            return DealMetrics(
                purchase_price=price,
                units=units,
                down_payment=down_payment,
                loan_amount=loan_amount,
                annual_loan_payment=annual_debt_service,
                gross_potential_rent_annual=gross_annual_rent,
                effective_gross_income=egi,
                total_operating_expenses=total_opex,
                noi=noi,
                cap_rate=cap_rate,
                cash_on_cash=cash_on_cash,
                annual_cash_flow=annual_cash_flow,
                dscr=dscr,
                grm=grm,
                price_per_unit=price_per_unit,
                va_noi=va_metrics["va_noi"],
                va_cash_on_cash=va_metrics["va_cash_on_cash"],
                va_cap_rate=va_metrics["va_cap_rate"],
                irr_5yr=irr,
                equity_multiple_5yr=equity_mult,
                total_profit_5yr=total_profit,
                exit_value=exit_value,
                passes_hurdle=passes,
                hurdle_reason=reason,
                red_flags=red_flags,
                value_add_signals=value_add_signals,
            )

        except Exception as e:
            logger.error(f"Financial analysis failed for {listing.external_id}: {e}")
            return None

    def _estimate_gross_rent(self, listing: RawListing) -> float:
        """
        Use listed rent if available, otherwise estimate from DFW market rates.
        DFW Class B/C average rents (2026): $950-1,200/unit/month
        """
        if listing.gross_monthly_rent and listing.gross_monthly_rent > 0:
            return listing.gross_monthly_rent

        # Back-calculate from listed NOI if available
        if listing.annual_noi and listing.annual_noi > 0:
            # Reverse-engineer: NOI = EGI - Expenses, EGI = GR * (1 - vacancy)
            # Rough estimate: expenses ≈ 40% of gross rent
            estimated_gross_annual = listing.annual_noi / 0.50
            return estimated_gross_annual / 12

        # Back-calculate from listed cap rate
        if listing.cap_rate_listed and listing.cap_rate_listed > 0:
            implied_noi = listing.price * listing.cap_rate_listed
            estimated_gross_annual = implied_noi / 0.50
            return estimated_gross_annual / 12

        # Market estimate: DFW Class B/C average $1,050/unit/month
        # Adjust by year built
        if listing.year_built:
            if listing.year_built >= 2000:
                rent_per_unit = 1_200
            elif listing.year_built >= 1990:
                rent_per_unit = 1_100
            elif listing.year_built >= 1980:
                rent_per_unit = 1_000
            else:
                rent_per_unit = 900
        else:
            rent_per_unit = 1_000  # Conservative default

        return rent_per_unit * listing.units

    def _calculate_annual_debt_service(self, loan_amount: float) -> float:
        """Monthly mortgage payment × 12."""
        r = self.c["interest_rate"] / 12   # monthly rate
        n = self.c["amortization_years"] * 12  # total payments
        if r == 0:
            monthly = loan_amount / n
        else:
            monthly = loan_amount * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
        return monthly * 12

    def _calculate_value_add(
        self,
        price: float,
        units: int,
        current_noi: float,
        down_payment: float,
        annual_debt_service: float,
        listing: RawListing,
    ) -> dict:
        """Project returns after value-add renovation."""
        rent_bump = self.c["value_add_rent_bump_pct"]
        reno_cost = units * self.c["reno_cost_per_unit"]

        # Total capital deployed = down payment + renovation costs
        total_equity = down_payment + reno_cost

        # Stabilized NOI after rent bump (expenses stay roughly same)
        va_noi = current_noi * (1 + rent_bump)
        va_cap_rate = va_noi / price if price > 0 else 0

        # CoC post-reno (using stabilized NOI)
        va_cash_flow = va_noi - annual_debt_service
        va_coc = va_cash_flow / total_equity if total_equity > 0 else 0

        return {
            "va_noi": va_noi,
            "va_cap_rate": va_cap_rate,
            "va_cash_on_cash": va_coc,
            "total_equity_deployed": total_equity,
            "reno_cost": reno_cost,
        }

    def _calculate_5yr_hold(
        self,
        price: float,
        down_payment: float,
        loan_amount: float,
        stabilized_noi: float,
        annual_debt_service: float,
    ) -> tuple[float, float, float, float]:
        """
        Calculate IRR and equity multiple over a 5-year hold.
        Assumes property is stabilized by year 1.
        """
        hold_years = self.c["hold_years"]
        exit_cap = self.c["exit_cap_rate"]
        selling_costs = self.c["selling_costs_pct"]

        # Annual cash flows (simplified: stabilized from year 1)
        # Grow NOI at 2% per year (DFW rent growth assumption)
        noi_growth_rate = 0.02
        annual_cash_flows = []
        for yr in range(1, hold_years + 1):
            yr_noi = stabilized_noi * ((1 + noi_growth_rate) ** yr)
            annual_cash_flows.append(yr_noi - annual_debt_service)

        # Exit value
        exit_noi = stabilized_noi * ((1 + noi_growth_rate) ** hold_years)
        gross_exit_value = exit_noi / exit_cap
        net_exit_proceeds = gross_exit_value * (1 - selling_costs)

        # Remaining loan balance after hold_years
        remaining_balance = self._remaining_loan_balance(loan_amount, hold_years)
        net_proceeds_after_payoff = net_exit_proceeds - remaining_balance

        # IRR calculation
        # Cash flows: initial investment (negative), then annual CF + final exit
        cash_flows = [-down_payment]
        for i, cf in enumerate(annual_cash_flows):
            if i == len(annual_cash_flows) - 1:
                cash_flows.append(cf + net_proceeds_after_payoff)
            else:
                cash_flows.append(cf)

        irr = self._calculate_irr(cash_flows)
        total_distributions = sum(annual_cash_flows) + net_proceeds_after_payoff
        equity_multiple = (total_distributions + down_payment) / down_payment if down_payment > 0 else 0
        total_profit = total_distributions

        return irr, equity_multiple, total_profit, gross_exit_value

    def _remaining_loan_balance(self, loan_amount: float, years: int) -> float:
        """Remaining principal after `years` of payments."""
        r = self.c["interest_rate"] / 12
        n_total = self.c["amortization_years"] * 12
        n_paid = years * 12
        if r == 0:
            return loan_amount * (1 - n_paid / n_total)
        monthly = loan_amount * (r * (1 + r) ** n_total) / ((1 + r) ** n_total - 1)
        balance = loan_amount * (1 + r) ** n_paid - monthly * ((1 + r) ** n_paid - 1) / r
        return max(0, balance)

    def _calculate_irr(self, cash_flows: list[float]) -> float:
        """Newton-Raphson IRR calculation."""
        def npv(rate, flows):
            return sum(cf / (1 + rate) ** i for i, cf in enumerate(flows))

        def npv_derivative(rate, flows):
            return sum(-i * cf / (1 + rate) ** (i + 1) for i, cf in enumerate(flows) if i > 0)

        rate = 0.15  # Initial guess
        for _ in range(100):
            npv_val = npv(rate, cash_flows)
            deriv = npv_derivative(rate, cash_flows)
            if abs(deriv) < 1e-10:
                break
            rate_new = rate - npv_val / deriv
            if abs(rate_new - rate) < 1e-8:
                return rate_new
            rate = rate_new
            if rate < -0.99 or rate > 10:
                return 0.0
        return max(0.0, rate)

    def _check_hurdles(
        self,
        coc: float,
        va_coc: float,
        cap_rate: float,
        irr: float,
        equity_mult: float,
        price_per_unit: float,
    ) -> tuple[bool, str]:
        """Check if deal meets any investment hurdle."""
        reasons = []

        if va_coc >= self.c["min_cash_on_cash"]:
            reasons.append(f"✅ Value-add CoC: {va_coc*100:.1f}% ≥ {self.c['min_cash_on_cash']*100:.0f}%")
        elif coc >= self.c["min_cash_on_cash"]:
            reasons.append(f"✅ As-is CoC: {coc*100:.1f}% ≥ {self.c['min_cash_on_cash']*100:.0f}%")

        if irr >= self.c["min_irr_5yr"]:
            reasons.append(f"✅ 5yr IRR: {irr*100:.1f}% ≥ {self.c['min_irr_5yr']*100:.0f}%")

        if cap_rate >= self.c["min_cap_rate"]:
            reasons.append(f"✅ Cap Rate: {cap_rate*100:.1f}% ≥ {self.c['min_cap_rate']*100:.0f}%")

        if price_per_unit <= self.c["max_price_per_unit"]:
            reasons.append(f"✅ Price/unit: ${price_per_unit:,.0f} ≤ ${self.c['max_price_per_unit']:,}")

        passes = len(reasons) >= 2  # Must meet at least 2 hurdles
        reason = " | ".join(reasons) if reasons else (
            f"❌ Did not meet hurdles: CoC={va_coc*100:.1f}%, IRR={irr*100:.1f}%, Cap={cap_rate*100:.1f}%"
        )
        return passes, reason

    def _identify_red_flags(
        self,
        listing: RawListing,
        cap_rate: float,
        dscr: float,
        price_per_unit: float,
        monthly_rent_total: float,
    ) -> list[str]:
        flags = []

        if dscr < 1.0:
            flags.append(f"DSCR below 1.0 ({dscr:.2f}) - negative cash flow even at full occupancy")
        elif dscr < 1.15:
            flags.append(f"Thin DSCR ({dscr:.2f}) - little margin for expense surprises")

        if price_per_unit > self.c["max_price_per_unit"]:
            flags.append(f"Price/unit (${price_per_unit:,.0f}) above target max (${self.c['max_price_per_unit']:,})")

        if cap_rate < 0.05:
            flags.append(f"Listed cap rate below 5% - may be overstated or expenses underreported")

        if listing.year_built and listing.year_built < 1970:
            flags.append(f"Built in {listing.year_built} - major systems (roof, plumbing, electrical) likely need replacement")

        if listing.days_on_market and listing.days_on_market > 120:
            flags.append(f"On market {listing.days_on_market} days - may have issues or seller is unrealistic on price")

        if listing.occupancy_rate and listing.occupancy_rate < 0.70:
            flags.append(f"Low occupancy ({listing.occupancy_rate*100:.0f}%) - turnaround risk")

        if listing.cap_rate_listed and listing.cap_rate_listed > 0.12:
            flags.append("Cap rate >12% may indicate distress, deferred maintenance, or bad location")

        return flags

    def _identify_value_add_signals(self, listing: RawListing) -> list[str]:
        signals = []
        desc = (listing.description or "").lower()

        keywords = {
            "below market rents": "Below-market rents - rent upside opportunity",
            "value add": "Seller identifies as value-add",
            "value-add": "Seller identifies as value-add",
            "upside": "Rent upside mentioned",
            "deferred maintenance": "Deferred maintenance - renovation opportunity",
            "reno": "Renovation opportunity mentioned",
            "repositioning": "Repositioning play",
            "mismanaged": "Mismanagement - operational upside",
            "estate": "Estate sale - motivated seller",
            "motivated": "Motivated seller",
            "1031": "1031 exchange - potential for negotiation",
        }

        for keyword, signal in keywords.items():
            if keyword in desc and signal not in signals:
                signals.append(signal)

        if listing.occupancy_rate and listing.occupancy_rate < 0.85:
            signals.append(f"Low occupancy ({listing.occupancy_rate*100:.0f}%) - rent roll upside")

        if listing.year_built and 1975 <= listing.year_built <= 1995:
            signals.append(f"Built {listing.year_built} - classic value-add vintage")

        return signals
