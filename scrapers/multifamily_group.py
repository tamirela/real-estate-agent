"""Scraper for MultifamilyGrp.com — WordPress site listing multifamily properties."""

import re
import logging
from typing import Optional, List

from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_URL = "https://multifamilygrp.com/listings/"


class MultifamilyGroupScraper(BaseScraper):
    """Scrapes multifamily property listings from multifamilygrp.com."""

    def scrape(self, markets: Optional[list] = None) -> list:
        all_listings = []  # type: List[RawListing]

        resp = self.get(BASE_URL)
        if resp is None:
            logger.warning("[MultifamilyGroup] No response from listings page")
            return all_listings

        soup = BeautifulSoup(resp.text, "html.parser")

        # Site uses H3 headers for each property, followed by metadata paragraphs
        h3_tags = soup.find_all("h3")
        if not h3_tags:
            logger.warning("[MultifamilyGroup] No H3 property headers found")
            return all_listings

        for h3 in h3_tags:
            try:
                name = h3.get_text(strip=True)
                if not name or len(name) < 3:
                    continue

                # Gather sibling text until next h3
                meta_text = ""
                link_url = ""
                sibling = h3.find_next_sibling()
                while sibling and sibling.name != "h3":
                    meta_text += " " + sibling.get_text(" ", strip=True)
                    a_tag = sibling.find("a", href=True)
                    if a_tag and not link_url:
                        link_url = a_tag["href"]
                    sibling = sibling.find_next_sibling()

                # Skip closed/sold
                if "closed" in meta_text.lower() or "sold" in meta_text.lower():
                    continue

                # Parse location
                city, state, zip_code = self._parse_location_from_text(meta_text, None, None, None)
                if state and state.upper() not in ("TX", "TEXAS"):
                    continue

                units = self._extract_units(meta_text)
                price = self._extract_price(meta_text)
                year_built = self._extract_year_built(meta_text)

                if not link_url:
                    link_url = f"{BASE_URL}#{name.replace(' ', '-').lower()}"

                external_id = f"mfg-{name.replace(' ', '-').lower()}"[:80]

                all_listings.append(RawListing(
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
                    raw_data={},
                ))
            except Exception as e:
                logger.warning(f"[MultifamilyGroup] Error parsing H3 listing: {e}")
                continue

        logger.info(f"[MultifamilyGroup] Found {len(all_listings)} listings")
        return all_listings

    def _parse_card(self, card) -> Optional[RawListing]:
        """Parse a single property card into a RawListing, filtering for Texas only."""

        # Extract link
        link_tag = card.find("a", href=True)
        url = link_tag["href"] if link_tag else ""
        if url and not url.startswith("http"):
            url = "https://multifamilygrp.com" + url

        # Extract property name / title
        title_tag = (
            card.find(["h2", "h3", "h4"])
            or card.find(class_=re.compile(r"title|name", re.I))
        )
        property_name = title_tag.get_text(strip=True) if title_tag else ""

        # Extract full text for parsing
        full_text = card.get_text(" ", strip=True)

        # Extract address components
        address = self._extract_field(card, ["address", "street", "location"]) or property_name
        city = self._extract_field(card, ["city"])
        state = self._extract_field(card, ["state"])
        zip_code = self._extract_field(card, ["zip", "postal"])

        # Try to parse address from text if structured fields not found
        if not city or not state:
            city, state, zip_code = self._parse_location_from_text(full_text, city, state, zip_code)

        # Filter: Texas only
        if state and state.upper() not in ("TX", "TEXAS"):
            return None

        # Extract numeric fields
        price = self._extract_price(full_text)
        units = self._extract_units(full_text)

        # Skip if we have basically no useful data
        if not property_name and not address:
            return None

        external_id = f"mfg-{property_name or address}".replace(" ", "-").lower()[:80]

        return RawListing(
            source="multifamily_group",
            external_id=external_id,
            url=url,
            address=address or property_name,
            city=city or "",
            state=state or "TX",
            zip_code=zip_code or "",
            price=price or 0.0,
            units=units or 0,
            year_built=self._extract_year_built(full_text),
            gross_monthly_rent=None,
            annual_noi=None,
            cap_rate_listed=self._extract_cap_rate(full_text),
            price_per_unit=None,
            sqft=self._extract_sqft(full_text),
            lot_sqft=None,
            description=full_text[:500],
            listing_date=None,
            days_on_market=None,
            property_class=None,
            occupancy_rate=None,
            raw_data={},
        )

    # ──────────────────────────────────────────────
    # Parsing helpers
    # ──────────────────────────────────────────────

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
        # Pattern: "City, TX 75XXX" or "City, Texas 75XXX"
        match = re.search(r'([A-Za-z\s]+),\s*(TX|Texas)\s*(\d{5})?', text, re.I)
        if match:
            city = city or match.group(1).strip()
            state = state or "TX"
            zip_code = zip_code or (match.group(3) or "")
        return city, state, zip_code

    @staticmethod
    def _extract_price(text: str) -> Optional[float]:
        """Extract price from text like '$4,500,000' or '$4.5M'."""
        # Try $X,XXX,XXX format
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
        """Extract unit count from text like '48 units' or '48-unit'."""
        match = re.search(r'(\d+)\s*[-\s]?\s*units?', text, re.I)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _extract_year_built(text: str) -> Optional[int]:
        """Extract year built from text like 'Built 1988' or 'Year Built: 1988'."""
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
