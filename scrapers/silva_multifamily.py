"""Scraper for SilvaMultifamily.com — Marcus & Millichap team focused on DFW workforce housing."""

import re
import logging
from typing import Optional, List

from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_URL = "https://silvamultifamily.com/"
# The actual listings page is /availableproperties (no hyphen, no trailing slash).
LISTINGS_URL = "https://silvamultifamily.com/availableproperties"


class SilvaMultifamilyScraper(BaseScraper):
    """Scrapes multifamily listings from silvamultifamily.com (DFW workforce housing)."""

    def scrape(self, markets: Optional[list] = None) -> list:
        all_listings: List[RawListing] = []

        resp = self.get(LISTINGS_URL)
        if resp is None:
            logger.warning("[SilvaMultifamily] No response from %s", LISTINGS_URL)
            return all_listings

        soup = BeautifulSoup(resp.text, "html.parser")

        # Each property is a Bootstrap card: div.card.border-color-dark
        cards = soup.select("div.card.border-color-dark")

        if not cards:
            logger.warning("[SilvaMultifamily] No property cards found on %s", LISTINGS_URL)
            return all_listings

        logger.info("[SilvaMultifamily] Found %d cards on %s", len(cards), LISTINGS_URL)

        for card in cards:
            parsed = self._parse_card(card)
            if parsed is not None:
                all_listings.append(parsed)

        logger.info("[SilvaMultifamily] Parsed %d listings", len(all_listings))
        return all_listings

    def _parse_card(self, card) -> Optional[RawListing]:
        """Parse a single div.card element into a RawListing.

        Structure:
          <div class="card border-color-dark mb-3">
            <div class="card-body">
              <div class="row">
                <div class="col-lg-7">
                  <h4 class="text-white font-weight-bold m-0">Property Name</h4>
                  <h3 class="font-weight-bold">Under Contract</h3>  (optional)
                  <img src="assets/NNN_bannerimage.jpg">
                </div>
                <div class="col-lg-5">
                  <table class="table">
                    <tr><th>City/State</th><td>Dallas, TX</td></tr>
                    <tr><th>Price Guidance</th><td>TBD by Market</td></tr>
                    <tr><th># of Units</th><td>216</td></tr>
                    <tr><th>Sq. ft.</th><td>151,740</td></tr>
                    <tr><th>Year Built</th><td>1984</td></tr>
                    <tr><th>Call For Offers Date</th><td>1/29/2026</td></tr>
                  </table>
                </div>
              </div>
            </div>
          </div>
        """
        # --- property name from h4 ---
        h4 = card.find("h4")
        property_name = h4.get_text(strip=True) if h4 else ""
        if not property_name:
            return None

        # --- status (e.g. "Under Contract") from h3 ---
        h3 = card.find("h3")
        status = h3.get_text(strip=True) if h3 else "Active"

        # --- structured data from the table ---
        table_data = {}
        table = card.find("table")
        if table:
            for row in table.find_all("tr"):
                th = row.find("th")
                td = row.find("td")
                if th and td:
                    key = th.get_text(strip=True).lower()
                    val = td.get_text(strip=True)
                    table_data[key] = val

        city_state_raw = table_data.get("city/state", "")
        city, state, zip_code = self._parse_location_field(city_state_raw)
        units = self._safe_int(table_data.get("# of units", ""))
        sqft = self._safe_int(table_data.get("sq. ft.", ""))
        year_built = self._safe_int(table_data.get("year built", ""))
        if year_built and not (1900 <= year_built <= 2030):
            year_built = None

        price_raw = table_data.get("price guidance", "")
        price = self._parse_price(price_raw)

        offers_date = table_data.get("call for offers date", "")

        # --- deal room link ---
        deal_link = card.find("a", href=True)
        url = deal_link["href"] if deal_link else LISTINGS_URL
        if url and not url.startswith("http"):
            url = "https://silvamultifamily.com/" + url.lstrip("/")

        external_id = f"silva-{property_name}".replace(" ", "-").lower()[:80]

        desc_parts = [property_name, city_state_raw, f"{units or '?'} units"]
        if sqft:
            desc_parts.append(f"{sqft:,} sqft")
        if year_built:
            desc_parts.append(f"built {year_built}")
        if status and status != "Active":
            desc_parts.append(f"({status})")
        if offers_date:
            desc_parts.append(f"offers due {offers_date}")

        return RawListing(
            source="silva_multifamily",
            external_id=external_id,
            url=url,
            address=property_name,  # Silva uses property names, not street addresses
            city=city or "",
            state=state or "TX",
            zip_code=zip_code or "",
            price=price or 0.0,
            units=units or 0,
            year_built=year_built,
            gross_monthly_rent=None,
            annual_noi=None,
            cap_rate_listed=None,
            price_per_unit=None,
            sqft=sqft,
            lot_sqft=None,
            description=" | ".join(desc_parts),
            listing_date=None,
            days_on_market=None,
            property_class=None,
            occupancy_rate=None,
            raw_data={"status": status, "offers_date": offers_date, **table_data},
        )

    # ──────────────────────────────────────────────
    # Parsing helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _parse_location_field(raw: str):
        """Parse 'City, TX 75001' into (city, state, zip)."""
        match = re.search(r'([A-Za-z\s]+),\s*(TX|Texas)\s*(\d{5})?', raw, re.I)
        if match:
            return match.group(1).strip(), "TX", match.group(3) or ""
        return "", "TX", ""

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """Parse price guidance like '$4,500,000' or 'TBD by Market'."""
        match = re.search(r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M)?', text, re.I)
        if match:
            raw = match.group(1).replace(",", "")
            val = float(raw)
            rest = text[match.end():match.end() + 15].lower()
            if "million" in rest or "m" in rest:
                if val < 1000:
                    val *= 1_000_000
            return val
        return None

    @staticmethod
    def _safe_int(val: str) -> Optional[int]:
        """Convert a string to int, stripping commas. Returns None on failure."""
        try:
            return int(val.replace(",", "").strip())
        except (ValueError, AttributeError):
            return None
