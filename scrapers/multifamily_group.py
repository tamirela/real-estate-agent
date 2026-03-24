"""Scraper for MultifamilyGrp.com — Avada/Fusion WordPress site listing multifamily properties."""

import re
import logging
import requests
from typing import Optional, List

from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, RawListing
from config import SCRAPER

logger = logging.getLogger(__name__)

BASE_URL = "https://multifamilygrp.com/listings/"

# Statuses that indicate a deal is no longer active
_INACTIVE_STATUSES = {"closed", "sold", "withdrawn", "cancelled"}


class MultifamilyGroupScraper(BaseScraper):
    """Scrapes multifamily property listings from multifamilygrp.com.

    This site blocks cloudscraper's TLS fingerprint (403), but responds
    fine to plain ``requests``.  We override __init__ to swap in a vanilla
    requests.Session while keeping all other BaseScraper behaviour.
    """

    def __init__(self):
        super().__init__()
        # Replace cloudscraper session with plain requests — the site's WAF
        # rejects cloudscraper but allows standard requests with a browser UA.
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": SCRAPER["user_agent"],
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })

    # ──────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────

    def scrape(self, markets: Optional[list] = None) -> list:
        all_listings: List[RawListing] = []

        resp = self.get(BASE_URL)
        if resp is None:
            logger.warning("[MultifamilyGroup] No response from listings page")
            return all_listings

        soup = BeautifulSoup(resp.text, "html.parser")

        # Avada/Fusion layout: each listing is an H3 with class
        # "fusion-title-heading" inside a fusion-title div.  Sibling divs
        # hold metadata (fusion-text) and document links.
        h3_tags = soup.find_all("h3", class_="fusion-title-heading")
        if not h3_tags:
            logger.warning("[MultifamilyGroup] No fusion-title H3 headers found")
            return all_listings

        for h3 in h3_tags:
            try:
                listing = self._parse_listing(h3)
                if listing is not None:
                    all_listings.append(listing)
            except Exception as e:
                logger.debug(f"[MultifamilyGroup] Skipping H3: {e}")
                continue

        logger.info(f"[MultifamilyGroup] Found {len(all_listings)} active TX listings "
                     f"(out of {len(h3_tags)} total)")
        return all_listings

    # ──────────────────────────────────────────────
    # Single-listing parser
    # ──────────────────────────────────────────────

    def _parse_listing(self, h3) -> Optional[RawListing]:
        """Parse a single H3 listing element.  Returns None if the listing
        should be filtered out (non-TX, closed, land, etc.)."""

        name = h3.get_text(strip=True)
        if not name or len(name) < 3:
            return None

        # The H3 lives inside a fusion-title wrapper div.
        # Metadata and links are sibling divs of that wrapper.
        title_div = h3.parent  # div.fusion-title
        meta_text = ""
        link_url = ""

        sibling = title_div.find_next_sibling()
        while sibling:
            text = sibling.get_text(" ", strip=True)
            meta_text += " " + text

            # Grab first link (usually Crexi / LoopNet)
            if not link_url:
                a_tag = sibling.find("a", href=True)
                if a_tag:
                    href = a_tag["href"]
                    if href.startswith("http"):
                        link_url = href

            sibling = sibling.find_next_sibling()
            # Stop if we hit another fusion-title (next listing)
            if sibling and sibling.find("h3", class_="fusion-title-heading"):
                break

        meta_text = meta_text.strip()

        # --- Filter: skip inactive deals ---
        status = self._extract_field_value(meta_text, "Status")
        if status:
            status_lower = status.lower()
            if any(s in status_lower for s in _INACTIVE_STATUSES):
                return None

        # --- Filter: Texas only ---
        location = self._extract_field_value(meta_text, "Location")
        city, state, zip_code = self._parse_location(location or meta_text)
        if state and state.upper() not in ("TX", "TEXAS"):
            return None
        # If we couldn't determine state at all, skip (likely non-property H3)
        if not state and not location:
            return None

        # --- Filter: skip land / acreage listings ---
        size_str = self._extract_field_value(meta_text, "Size") or ""
        if re.search(r'acres?|lots?\b|land\b', size_str, re.I):
            return None

        units = self._extract_units(size_str) or self._extract_units(meta_text)
        price = self._extract_price(meta_text)
        year_built = self._extract_year_built(meta_text)

        if not link_url:
            link_url = f"{BASE_URL}#{name.replace(' ', '-').lower()}"

        external_id = f"mfg-{name.replace(' ', '-').lower()}"[:80]

        return RawListing(
            source="multifamily_group",
            external_id=external_id,
            url=link_url,
            address=name,
            city=city or "",
            state=state or "TX",
            zip_code=zip_code or "",
            price=price or 0.0,
            units=units or 0,
            year_built=year_built,
            gross_monthly_rent=None,
            annual_noi=None,
            cap_rate_listed=self._extract_cap_rate(meta_text),
            price_per_unit=None,
            sqft=self._extract_sqft(meta_text),
            lot_sqft=None,
            description=f"{name} | {meta_text[:300]}",
            listing_date=None,
            days_on_market=None,
            property_class=None,
            occupancy_rate=None,
            raw_data={"status": status or ""},
        )

    # ──────────────────────────────────────────────
    # Parsing helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _extract_field_value(text: str, field_name: str) -> Optional[str]:
        """Extract the value after 'FieldName:' up to the next known field or end."""
        # Known fields on this site: Location, Size, Status, Year Built, Type
        pattern = rf'{field_name}\s*:\s*(.+?)(?:\s+(?:Location|Size|Status|Year Built|Type)\s*:|$)'
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _parse_location(text: str):
        """Extract city, state, zip from text like 'Dallas, TX 75201' or 'Location: Irving, TX'.

        Returns (city, state, zip).  *state* is the 2-letter abbreviation if
        found, or None if no state could be determined.
        """
        # Try TX / Texas first (most common on this site)
        match = re.search(r'([A-Za-z\s.]+?),\s*(TX|Texas)\s*(\d{5})?', text, re.I)
        if match:
            city = match.group(1).strip()
            city = re.sub(r'^(?:Location\s*:?\s*)', '', city, flags=re.I).strip()
            return city, "TX", match.group(3) or ""

        # Try any US state abbreviation so we can detect (and later filter) non-TX
        match = re.search(
            r'([A-Za-z\s.]+?),\s*([A-Z]{2})\s*(\d{5})?', text
        )
        if match:
            city = match.group(1).strip()
            city = re.sub(r'^(?:Location\s*:?\s*)', '', city, flags=re.I).strip()
            return city, match.group(2), match.group(3) or ""

        return None, None, ""

    @staticmethod
    def _extract_field(card, keywords) -> Optional[str]:
        """Try to find a field value by class/data-attribute keywords."""
        for kw in keywords:
            el = card.find(class_=re.compile(kw, re.I))
            if el:
                return el.get_text(strip=True)
            el = card.find(attrs={"data-field": re.compile(kw, re.I)})
            if el:
                return el.get_text(strip=True)
        return None

    @staticmethod
    def _parse_location_from_text(text: str, city: Optional[str], state: Optional[str],
                                   zip_code: Optional[str]):
        """Try to extract city, state, zip from free text using common patterns."""
        match = re.search(r'([A-Za-z\s]+),\s*(TX|Texas)\s*(\d{5})?', text, re.I)
        if match:
            city = city or match.group(1).strip()
            state = state or "TX"
            zip_code = zip_code or (match.group(3) or "")
        return city, state, zip_code

    @staticmethod
    def _extract_price(text: str) -> Optional[float]:
        """Extract price from text like '$4,500,000' or '$4.5M'."""
        match = re.search(r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M)?', text, re.I)
        if match:
            raw = match.group(1).replace(",", "")
            val = float(raw)
            if "million" in text[match.start():match.end() + 10].lower() or "M" in match.group(0):
                if val < 1000:  # e.g. $4.5M means 4.5 million
                    val *= 1_000_000
            return val
        return None

    @staticmethod
    def _extract_units(text: str) -> Optional[int]:
        """Extract unit count from text like '48 units', '48-unit', '374 Beds', or bare '40'."""
        # Try explicit "X units" or "X beds" first
        match = re.search(r'(\d+)\s*[-\s]?\s*(?:units?|beds?|townhomes?)', text, re.I)
        if match:
            return int(match.group(1))
        # Bare number (from Size field)
        match = re.match(r'^\s*(\d+)\s*$', text.strip())
        if match:
            val = int(match.group(1))
            if 2 <= val <= 5000:  # reasonable unit count range
                return val
        return None

    @staticmethod
    def _extract_year_built(text: str) -> Optional[int]:
        """Extract year built from text like 'Year Built: 1988' or 'Built 1988'.
        For ranges like '1984-2000', returns the earliest year."""
        match = re.search(r'(?:built|year\s*built|constructed|vintage)[:\s]*(\d{4})', text, re.I)
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2030:
                return year
        return None

    @staticmethod
    def _extract_cap_rate(text: str) -> Optional[float]:
        """Extract cap rate from text like '7.5% cap' or 'Cap Rate: 7.5%'."""
        match = re.search(r'(?:cap\s*rate|cap)[:\s]*([\d.]+)\s*%', text, re.I)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def _extract_sqft(text: str) -> Optional[int]:
        """Extract square footage from text."""
        match = re.search(r'([\d,]+)\s*(?:sq\.?\s*ft|SF|square\s*feet)', text, re.I)
        if match:
            return int(match.group(1).replace(",", ""))
        return None
