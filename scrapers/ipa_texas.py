"""Scraper for IPA Texas Multifamily (ipatexasmultifamily.com) — Institutional Property Advisors."""

import re
import logging
from typing import Optional, List

from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_URL = "https://ipatexasmultifamily.com/properties/"


class IpaTexasScraper(BaseScraper):
    """Scrapes multifamily listings from IPA Texas Multifamily."""

    def scrape(self, markets: Optional[list] = None) -> list:
        all_listings: List[RawListing] = []

        resp = self.get(BASE_URL)
        if resp is None:
            logger.warning("[IpaTexas] No response from properties page")
            return all_listings

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try common WordPress / property listing selectors
        cards = self._find_property_cards(soup)

        if not cards:
            logger.warning("[IpaTexas] No property cards found — trying detail link approach")
            # Fallback: find links to individual property pages
            cards = self._find_property_links_as_cards(soup)

        for card in cards:
            parsed = self._parse_card(card)
            if parsed is not None:
                all_listings.append(parsed)

        logger.info(f"[IpaTexas] Found {len(all_listings)} listings")
        return all_listings

    @staticmethod
    def _find_property_cards(soup):
        """Try common property card selectors in priority order."""
        selectors = [
            ".property-card",
            ".listing-card",
            ".property-item",
            ".property",
            "[class*='property']",
            "[class*='listing']",
            "article",
            ".entry",
            ".post",
            ".elementor-widget-container",
            ".wp-block-group",
        ]
        for sel in selectors:
            cards = soup.select(sel)
            # Filter out very short elements (nav items, etc.)
            cards = [c for c in cards if len(c.get_text(strip=True)) > 20]
            if cards:
                return cards
        return []

    def _find_property_links_as_cards(self, soup):
        """Find links to property pages and scrape each detail page."""
        detail_cards = []
        seen_urls = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)

            # Look for links that seem like property detail pages
            if any(kw in href.lower() for kw in ["propert", "listing", "/available/"]):
                if href.startswith("/"):
                    href = "https://ipatexasmultifamily.com" + href
                if href.startswith("http") and href not in seen_urls:
                    seen_urls.add(href)
                    detail = self._fetch_detail_page(href, text)
                    if detail is not None:
                        detail_cards.append(detail)

            if len(detail_cards) >= 30:  # Safety cap
                break

        return detail_cards

    def _fetch_detail_page(self, url: str, link_text: str) -> Optional[dict]:
        """Fetch a detail page and return a dict with extracted data."""
        resp = self.get(url)
        if resp is None:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        # Must have some property-related content
        if not any(kw in full_text.lower() for kw in ["unit", "apartment", "multifamily", "price", "sqft"]):
            return None

        return {"soup": soup, "url": url, "link_text": link_text, "full_text": full_text}

    def _parse_card(self, card) -> Optional[RawListing]:
        """Parse a property card (or detail page dict) into a RawListing."""

        # Handle both BeautifulSoup elements and dicts from detail page scraping
        if isinstance(card, dict):
            return self._parse_detail_dict(card)

        # Standard card parsing
        link_tag = card.find("a", href=True)
        url = link_tag["href"] if link_tag else ""
        if url and not url.startswith("http"):
            url = "https://ipatexasmultifamily.com" + url

        title_tag = card.find(["h2", "h3", "h4"])
        property_name = title_tag.get_text(strip=True) if title_tag else ""

        full_text = card.get_text(" ", strip=True)

        address = self._extract_address(card, full_text) or property_name
        city, state, zip_code = self._parse_location(full_text)
        price = self._extract_price(full_text)
        units = self._extract_units(full_text)

        if not property_name and not address:
            return None

        external_id = f"ipa-{property_name or address}".replace(" ", "-").lower()[:80]

        return RawListing(
            source="ipa_texas",
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

    def _parse_detail_dict(self, data: dict) -> Optional[RawListing]:
        """Parse a detail page data dict into a RawListing."""
        soup = data["soup"]
        url = data["url"]
        full_text = data["full_text"]
        link_text = data["link_text"]

        title = soup.find("h1")
        property_name = title.get_text(strip=True) if title else link_text

        address = self._extract_address(soup, full_text) or property_name
        city, state, zip_code = self._parse_location(full_text)

        if not property_name and not address:
            return None

        external_id = f"ipa-{property_name or address}".replace(" ", "-").lower()[:80]

        return RawListing(
            source="ipa_texas",
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
    def _extract_address(element, text: str) -> Optional[str]:
        """Try to extract street address."""
        addr_el = element.find(class_=re.compile(r"address|street|location", re.I))
        if addr_el:
            return addr_el.get_text(strip=True)
        match = re.search(
            r'\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Rd|Road|Ln|Lane|Ct|Court|Way|Pkwy|Parkway)',
            text,
        )
        if match:
            return match.group(0).strip()
        return None

    @staticmethod
    def _parse_location(text: str):
        """Extract city, state, zip from text."""
        match = re.search(r'([A-Za-z\s]+),\s*(TX|Texas)\s*(\d{5})?', text, re.I)
        if match:
            return match.group(1).strip(), "TX", match.group(3) or ""
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
