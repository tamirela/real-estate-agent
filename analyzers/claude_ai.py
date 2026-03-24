"""
Claude AI deal analyzer.
Uses Claude to write a natural-language deal summary, identify red flags,
and give a clear BUY / PASS / WATCH recommendation with reasoning.
"""

import json
import logging
from typing import Optional
import anthropic
from scrapers.base import RawListing
from analyzers.financials import DealMetrics
from config import API_KEYS, FINANCIAL_CRITERIA

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert multifamily real estate analyst specializing in
value-add deals in the Dallas-Fort Worth market. Your job is to analyze deals for a
syndicator/investor looking for Class B/C properties with 30+ units under $8M that
can generate 20%+ cash-on-cash returns after renovation.

Be direct, specific, and numbers-focused. Don't pad your analysis. Flag real risks
clearly. Give a definitive recommendation."""


class ClaudeAnalyzer:
    """Uses Claude claude-opus-4-6 to write deal memos and give recommendations."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=API_KEYS["anthropic"])

    def analyze(self, listing: RawListing, metrics: DealMetrics) -> Optional[dict]:
        """
        Generate a full deal memo using Claude.
        Returns dict with: recommendation, summary, risks, opportunities, memo_text.
        """
        if not API_KEYS["anthropic"]:
            logger.warning("No Anthropic API key - skipping AI analysis")
            return self._fallback_analysis(listing, metrics)

        prompt = self._build_prompt(listing, metrics)

        try:
            message = self.client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = message.content[0].text
            return self._parse_response(raw_text, metrics)

        except anthropic.AuthenticationError:
            logger.error("Invalid Anthropic API key")
            return self._fallback_analysis(listing, metrics)
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return self._fallback_analysis(listing, metrics)

    def _build_prompt(self, listing: RawListing, metrics: DealMetrics) -> str:
        m = metrics.to_dict()

        return f"""Analyze this DFW multifamily deal and give me your recommendation.

## PROPERTY
- Address: {listing.address}, {listing.city}, TX {listing.zip_code}
- Source: {listing.source.title()} | URL: {listing.url}
- Units: {listing.units} | Year Built: {listing.year_built or 'Unknown'}
- Property Class: {listing.property_class or 'Unknown (inferred Class B/C)'}
- Listing Price: ${listing.price:,.0f}
- Days on Market: {listing.days_on_market or 'Unknown'}
- Occupancy: {f"{listing.occupancy_rate*100:.0f}%" if listing.occupancy_rate else 'Not disclosed'}

## FINANCIAL SNAPSHOT (my analysis, 20% down @ 7.5%)
- Price/Unit: ${m['price_per_unit']:,.0f}
- Cap Rate (as-is): {m['cap_rate']}%
- NOI (as-is): ${m['noi']:,.0f}/yr
- Cash-on-Cash (as-is): {m['cash_on_cash']}%
- Annual Cash Flow: ${m['annual_cash_flow']:,.0f}
- DSCR: {m['dscr']}

## VALUE-ADD PROJECTIONS (20% rent bump, $8k/unit reno)
- Stabilized NOI: ${m['va_noi']:,.0f}/yr
- Value-Add CoC: {m['va_cash_on_cash']}%
- Value-Add Cap Rate: {m['va_cap_rate']}%

## 5-YEAR HOLD ANALYSIS (exit at 6.5% cap)
- IRR: {m['irr_5yr']}%
- Equity Multiple: {m['equity_multiple_5yr']}x
- Exit Value: ${m['exit_value']:,.0f}
- Total Profit: ${m['total_profit_5yr']:,.0f}

## FLAGS IDENTIFIED
Red Flags: {json.dumps(m['red_flags'], indent=2) if m['red_flags'] else 'None identified'}
Value-Add Signals: {json.dumps(m['value_add_signals'], indent=2) if m['value_add_signals'] else 'None identified'}

## LISTING DESCRIPTION
{listing.description[:800] if listing.description else 'Not provided'}

---
Please provide your analysis in this EXACT format:

RECOMMENDATION: [STRONG BUY / BUY / WATCH / PASS]

ONE_LINE: [One punchy sentence about why you recommend this or not]

SUMMARY:
[2-3 paragraphs. Cover: what makes this deal interesting or not, the value-add thesis, key risks, and whether the numbers hold up at these assumptions.]

TOP_RISKS:
- [Risk 1]
- [Risk 2]
- [Risk 3]

TOP_OPPORTUNITIES:
- [Opportunity 1]
- [Opportunity 2]
- [Opportunity 3]

DUE_DILIGENCE_PRIORITIES:
- [What to check first before making an offer]
- [Second priority]
- [Third priority]
"""

    def _parse_response(self, text: str, metrics: DealMetrics) -> dict:
        """Parse Claude's structured response into a dict."""
        result = {
            "recommendation": "WATCH",
            "one_line": "",
            "summary": "",
            "top_risks": [],
            "top_opportunities": [],
            "due_diligence": [],
            "full_memo": text,
        }

        lines = text.strip().split("\n")
        current_section = None
        buffer = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("RECOMMENDATION:"):
                rec = stripped.replace("RECOMMENDATION:", "").strip()
                for r in ["STRONG BUY", "BUY", "WATCH", "PASS"]:
                    if r in rec.upper():
                        result["recommendation"] = r
                        break

            elif stripped.startswith("ONE_LINE:"):
                result["one_line"] = stripped.replace("ONE_LINE:", "").strip()

            elif stripped.startswith("SUMMARY:"):
                current_section = "summary"
                buffer = []

            elif stripped.startswith("TOP_RISKS:"):
                if current_section == "summary":
                    result["summary"] = "\n".join(buffer).strip()
                current_section = "risks"
                buffer = []

            elif stripped.startswith("TOP_OPPORTUNITIES:"):
                if current_section == "risks":
                    result["top_risks"] = [b.lstrip("- ").strip() for b in buffer if b.strip()]
                current_section = "opportunities"
                buffer = []

            elif stripped.startswith("DUE_DILIGENCE_PRIORITIES:"):
                if current_section == "opportunities":
                    result["top_opportunities"] = [b.lstrip("- ").strip() for b in buffer if b.strip()]
                current_section = "dd"
                buffer = []

            else:
                if current_section:
                    buffer.append(stripped)

        # Flush last section
        if current_section == "dd" and buffer:
            result["due_diligence"] = [b.lstrip("- ").strip() for b in buffer if b.strip()]
        elif current_section == "summary" and buffer:
            result["summary"] = "\n".join(buffer).strip()

        return result

    def _fallback_analysis(self, listing: RawListing, metrics: DealMetrics) -> dict:
        """Simple rule-based analysis when Claude API is not available."""
        m = metrics.to_dict()
        coc = float(m["va_cash_on_cash"])
        cap = float(m["cap_rate"])
        irr = float(m["irr_5yr"])

        if coc >= 20 and irr >= 18:
            rec = "STRONG BUY"
            one_line = f"Hits all targets: {coc:.1f}% value-add CoC, {irr:.1f}% IRR."
        elif coc >= 20 or irr >= 18:
            rec = "BUY"
            one_line = f"Meets primary hurdle with {coc:.1f}% value-add CoC and {irr:.1f}% IRR."
        elif coc >= 15 or cap >= 0.065:
            rec = "WATCH"
            one_line = f"Close but needs price reduction or better rent data. CoC: {coc:.1f}%."
        else:
            rec = "PASS"
            one_line = f"Does not meet return thresholds. CoC: {coc:.1f}%, IRR: {irr:.1f}%."

        return {
            "recommendation": rec,
            "one_line": one_line,
            "summary": metrics.hurdle_reason,
            "top_risks": m["red_flags"],
            "top_opportunities": m["value_add_signals"],
            "due_diligence": [
                "Verify actual rent roll and current leases",
                "Get T-12 income and expense statement",
                "Inspect roof, HVAC, plumbing",
            ],
            "full_memo": f"{rec}: {one_line}\n\n{metrics.hurdle_reason}",
        }
