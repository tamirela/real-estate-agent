"""Scraper for IPA Texas Multifamily (ipatexasmultifamily.com) — Institutional Property Advisors."""

import re
import logging
from typing import Optional, List

from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

# Listings live on the homepage in div.slider__content cards, NOT on /properties/
# (the /properties/ page is a JS-driven filter shell with no server-rendered data).
BASE_URL = "https://ipatexasmultifamily.com/"


class IpaTexasScraper(BaseScraper):
    """Scrapes multifamily listings from IPA Texas Multifamily."""

    def __init__(self):
        super().__init__()
        # IPA's server returns Brotli-compressed responses that cloudscraper
        # can't decode (garbled binary). Drop 'br' so gzip/deflate are used.
        self.session.headers["Accept-Encoding"] = "gzip, deflate"

    def scrape(self, markets: Optional[list] = None) -> list:
        all_listings: List[RawListing] = []

        resp = self.get(BASE_URL)
        if resp is None:
            logger.warning("[IpaTexas] No response from homepage")
            return all_listings

        soup = BeautifulSoup(resp.text, "html.parser")

        # Property cards are div.slider__content on the homepage.
        # Each contains: h2 (name), ul>li (Location, Year Built, Units),
        # and <a> links to the OM and detail page.
        cards = soup.select("div.slider__content")

        if not cards:
            logger.warning("[IpaTexas] No slider__content cards found on homepage")
            return all_listings

        for card in cards:
            parsed = self._parse_slider_card(card)
            if parsed is not None:
                all_listings.append(parsed)

        logger.info(f"[IpaTexas] Found {len(all_listings)} listings")
        return all_listings

    def _parse_slider_card(self, card) -> Optional[RawListing]:
        """Parse a div.slider__content card into a RawListing.

        Structure:
          <div class="slider__content">
            <h2>Property Name</h2>
            <ul>
              <li>Location: City, TX</li>
              <li>Year Built: 2024</li>
              <li>Units: 336</li>
            </ul>
            <a class="btn btn--block" href="…">View Offering Memorandum …</a>
            <a class="btn btn--border" href="…/property/slug/">View Details</a>
          </div>
        """
        # --- property name ---
        h2 = card.find("h2")
        property_name = h2.get_text(strip=True) if h2 else ""
        if not property_name:
            return None

        # --- structured fields from <li> tags ---
        li_data = {}
        for li in card.find_all("li"):
            text = li.get_text(strip=True)
            if ":" in text:
                key, _, val = text.partition(":")
                li_data[key.strip().lower()] = val.strip()

        location_raw = li_data.get("location", "")
        city, state, zip_code = self._parse_location_field(location_raw)
        units = self._safe_int(li_data.get("units", ""))
        year_built = self._safe_int(li_data.get("year built", ""))
        if year_built and not (1900 <= year_built <= 2030):
            year_built = None

        # --- detail page URL ---
        detail_link = card.find("a", class_="btn--border")
        if detail_link and detail_link.get("href"):
            url = detail_link["href"]
        else:
            # Fallback: any link containing /property/
            url = ""
            for a in card.find_all("a", href=True):
                if "/property/" in a["href"]:
                    url = a["href"]
                    break
        if url and not url.startswith("http"):
            url = "https://ipatexasmultifamily.com" + url

        external_id = f"ipa-{property_name}".replace(" ", "-").lower()[:80]

        return RawListing(
            source="ipa_texas",
            external_id=external_id,
            url=url,
            address=property_name,  # IPA cards use property name, not street address
            city=city or "",
            state=state or "TX",
            zip_code=zip_code or "",
            price=0.0,  # IPA does not publish asking prices on the homepage
            units=units or 0,
            year_built=year_built,
            gross_monthly_rent=None,
            annual_noi=None,
            cap_rate_listed=None,
            price_per_unit=None,
            sqft=None,
            lot_sqft=None,
            description=f"{property_name} — {location_raw}, {units or '?'} units, built {year_built or '?'}",
            listing_date=None,
            days_on_market=None,
            property_class=None,
            occupancy_rate=None,
            raw_data=li_data,
        )

    # ──────────────────────────────────────────────
    # Parsing helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _parse_location_field(raw: str):
        """Parse a location string like 'Round Rock, TX' into (city, state, zip)."""
        match = re.search(r'([A-Za-z\s]+),\s*(TX|Texas)\s*(\d{5})?', raw, re.I)
        if match:
            return match.group(1).strip(), "TX", match.group(3) or ""
        return "", "TX", ""

    @staticmethod
    def _safe_int(val: str) -> Optional[int]:
        """Convert a string to int, stripping commas. Returns None on failure."""
        try:
            return int(val.replace(",", "").strip())
        except (ValueError, AttributeError):
            return None
