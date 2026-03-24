"""Scraper for SilvaMultifamily.com — Marcus & Millichap team focused on DFW workforce housing."""

import re
import logging
from typing import Optional, List

from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_URL = "https://silvamultifamily.com/"
LISTINGS_PATHS = [
    "listings/",
    "properties/",
    "available-properties/",
    "current-offerings/",
    "",  # Main page may have listings
]


class SilvaMultifamilyScraper(BaseScraper):
    """Scrapes multifamily listings from silvamultifamily.com (DFW workforce housing)."""

    def scrape(self, markets: Optional[list] = None) -> list:
        all_listings: List[RawListing] = []

        # Try multiple possible listing pages
        for path in LISTINGS_PATHS:
            url = BASE_URL + path
            resp = self.get(url)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = self._find_property_cards(soup)

            if cards:
                logger.info(f"[SilvaMultifamily] Found {len(cards)} cards on {url}")
                for card in cards:
                    parsed = self._parse_card(card, url)
                    if parsed is not None:
                        all_listings.append(parsed)
                break  # Found listings, no need to try other paths

        # If no structured cards found, try parsing links that look like property pages
        if not all_listings:
            resp = self.get(BASE_URL)
            if resp:
                soup = BeautifulSoup(resp.text, "html.parser")
                property_links = self._find_property_links(soup)
                for link_url, link_text in property_links:
                    parsed = self._scrape_detail_page(link_url, link_text)
                    if parsed is not None:
                        all_listings.append(parsed)

        logger.info(f"[SilvaMultifamily] Found {len(all_listings)} listings")
        return all_listings

    @staticmethod
    def _find_property_cards(soup):
        """Try common WordPress property card selectors."""
        selectors = [
            "article",
            ".property-card",
            ".listing-card",
            ".property-item",
            ".property",
            ".listing",
            "[class*='property']",
            "[class*='listing']",
            ".entry-content .wp-block-group",
            ".elementor-widget-container",
        ]
        for sel in selectors:
            cards = soup.select(sel)
            # Filter out navigation/header/footer articles
            cards = [c for c in cards if len(c.get_text(strip=True)) > 30]
            if cards:
                return cards
        return []

    @staticmethod
    def _find_property_links(soup) -> list:
        """Find links that look like they point to property detail pages."""
        results = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)
            # Look for links with property-related keywords
            if any(kw in href.lower() for kw in ["property", "listing", "unit", "apartment"]):
                if href.startswith("/"):
                    href = "https://silvamultifamily.com" + href
                if href.startswith("http"):
                    results.append((href, text))
        return results[:20]  # Cap at 20 to be polite

    def _scrape_detail_page(self, url: str, link_text: str) -> Optional[RawListing]:
        """Scrape a property detail page for listing data."""
        resp = self.get(url)
        if resp is None:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        # Check if page has any property-related content
        if not any(kw in full_text.lower() for kw in ["unit", "price", "sqft", "sq ft", "apartment", "multifamily"]):
            return None

        property_name = link_text or self._extract_title(soup)
        address = self._extract_address(soup, full_text) or property_name
        city, state, zip_code = self._parse_location(full_text)

        if not property_name and not address:
            return None

        external_id = f"silva-{property_name or address}".replace(" ", "-").lower()[:80]

        return RawListing(
            source="silva_multifamily",
            external_id=external_id,
            url=url,
            address=address or property_name,
            city=city or "",
            state=state or "TX",
            zip_code=zip_code or "",
            price=self._extract_price(full_text) or 0.0,
            units=self._extract_units(full_text) or 0,
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

    def _parse_card(self, card, page_url: str) -> Optional[RawListing]:
        """Parse a single property card element."""
        link_tag = card.find("a", href=True)
        url = link_tag["href"] if link_tag else page_url
        if url and not url.startswith("http"):
            url = "https://silvamultifamily.com" + url

        title_tag = card.find(["h2", "h3", "h4"])
        property_name = title_tag.get_text(strip=True) if title_tag else ""

        full_text = card.get_text(" ", strip=True)

        address = self._extract_address(card, full_text) or property_name
        city, state, zip_code = self._parse_location(full_text)

        if not property_name and not address:
            return None

        external_id = f"silva-{property_name or address}".replace(" ", "-").lower()[:80]

        return RawListing(
            source="silva_multifamily",
            external_id=external_id,
            url=url,
            address=address or property_name,
            city=city or "",
            state=state or "TX",
            zip_code=zip_code or "",
            price=self._extract_price(full_text) or 0.0,
            units=self._extract_units(full_text) or 0,
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
    def _extract_title(soup) -> str:
        title = soup.find("h1")
        if title:
            return title.get_text(strip=True)
        og_title = soup.find("meta", property="og:title")
        if og_title:
            return og_title.get("content", "")
        return ""

    @staticmethod
    def _extract_address(element, text: str) -> Optional[str]:
        """Try to extract street address."""
        # Look for address-classed element
        addr_el = element.find(class_=re.compile(r"address|street|location", re.I))
        if addr_el:
            return addr_el.get_text(strip=True)
        # Try regex for street address pattern
        match = re.search(r'\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Rd|Road|Ln|Lane|Ct|Court|Way|Pkwy|Parkway)', text)
        if match:
            return match.group(0).strip()
        return None

    @staticmethod
    def _parse_location(text: str):
        """Extract city, state, zip from text."""
        match = re.search(r'([A-Za-z\s]+),\s*(TX|Texas)\s*(\d{5})?', text, re.I)
        if match:
            return match.group(1).strip(), "TX", match.group(3) or ""
        # Default to DFW area since Silva focuses on DFW
        return "", "TX", ""

    @staticmethod
    def _extract_price(text: str) -> Optional[float]:
        match = re.search(r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M)?', text, re.I)
        if match:
            raw = match.group(1).replace(",", "")
            val = float(raw)
            if "million" in text[match.start():match.end() + 10].lower() or "M" in match.group(0):
                if val < 1000:
                    val *= 1_000_000
            return val
        return None

    @staticmethod
    def _extract_units(text: str) -> Optional[int]:
        match = re.search(r'(\d+)\s*[-\s]?\s*units?', text, re.I)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _extract_year_built(text: str) -> Optional[int]:
        match = re.search(r'(?:built|year\s*built|constructed|vintage)[:\s]*(\d{4})', text, re.I)
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2030:
                return year
        return None

    @staticmethod
    def _extract_cap_rate(text: str) -> Optional[float]:
        match = re.search(r'(?:cap\s*rate|cap)[:\s]*([\d.]+)\s*%', text, re.I)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def _extract_sqft(text: str) -> Optional[int]:
        match = re.search(r'([\d,]+)\s*(?:sq\.?\s*ft|SF|square\s*feet)', text, re.I)
        if match:
            return int(match.group(1).replace(",", ""))
        return None
