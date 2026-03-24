"""
Marcus & Millichap listing scraper.
MM is one of the top multifamily brokers in DFW - great source for off-market and listed deals.
"""

import re
import json
import logging
from typing import Optional
from bs4 import BeautifulSoup
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA

logger = logging.getLogger(__name__)


class MarcusMillichapScraper(BaseScraper):
    """Scrapes Marcus & Millichap for multifamily listings."""

    SEARCH_URL = "https://www.marcusmillichap.com/listings/search"
    API_URL = "https://www.marcusmillichap.com/api/listings"

    def scrape(self, markets: list[str]) -> list[RawListing]:
        logger.info("[Marcus & Millichap] Searching DFW multifamily...")
        listings = []
        try:
            listings = self._fetch_listings()
            logger.info(f"[Marcus & Millichap] Found {len(listings)} listings")
        except Exception as e:
            logger.error(f"[Marcus & Millichap] Scrape failed: {e}")
        return listings

    def _fetch_listings(self) -> list[RawListing]:
        payload = {
            "propertyType": ["Multifamily"],
            "transactionType": "sale",
            "location": "Dallas-Fort Worth, TX",
            "maxPrice": SEARCH_CRITERIA["max_price"],
            "minUnits": SEARCH_CRITERIA["min_units"],
            "page": 1,
            "pageSize": 100,
        }

        self.session.headers["Content-Type"] = "application/json"
        self.session.headers["X-Requested-With"] = "XMLHttpRequest"

        # Try the API endpoint first
        import time
        time.sleep(self.delay)
        try:
            response = self.session.post(
                self.API_URL,
                json=payload,
                timeout=self.timeout
            )
            if response.status_code == 200:
                data = response.json()
                items = data.get("listings", data.get("results", data.get("data", [])))
                return [r for r in (self._parse_item(i) for i in items) if r]
        except Exception:
            pass

        # Fallback: scrape the search page
        response = self.get(
            self.SEARCH_URL,
            params={
                "type": "Multifamily",
                "location": "Dallas-Fort Worth",
                "state": "TX",
                "maxPrice": SEARCH_CRITERIA["max_price"],
            }
        )
        if not response:
            return []

        return self._parse_html(response.text)

    def _parse_item(self, item: dict) -> Optional[RawListing]:
        try:
            price = float(str(item.get("price", item.get("listPrice", 0))).replace(",", "").replace("$", ""))
            units = int(item.get("units", item.get("numberOfUnits", 0)))

            if price > SEARCH_CRITERIA["max_price"] or units < SEARCH_CRITERIA["min_units"]:
                return None

            prop_id = str(item.get("id", item.get("listingId", "")))

            return RawListing(
                source="marcus_millichap",
                external_id=prop_id,
                url=item.get("url", f"https://www.marcusmillichap.com/listings/{prop_id}"),
                address=item.get("address", item.get("streetAddress", "")),
                city=item.get("city", ""),
                state=item.get("state", "TX"),
                zip_code=str(item.get("zip", item.get("postalCode", ""))),
                price=price,
                units=units,
                year_built=self._safe_int(item.get("yearBuilt")),
                gross_monthly_rent=self._safe_float(item.get("grossMonthlyRent")),
                annual_noi=self._safe_float(item.get("noi", item.get("annualNOI"))),
                cap_rate_listed=self._safe_float(item.get("capRate")),
                price_per_unit=price / units if units > 0 else None,
                sqft=self._safe_int(item.get("sqft", item.get("buildingSqFt"))),
                lot_sqft=None,
                description=item.get("description", ""),
                listing_date=item.get("listingDate", ""),
                days_on_market=self._safe_int(item.get("daysOnMarket")),
                property_class=item.get("propertyClass"),
                occupancy_rate=self._safe_float(item.get("occupancyRate")),
                raw_data=item,
            )
        except Exception as e:
            logger.debug(f"[MM] Parse error: {e}")
            return None

    def _parse_html(self, html: str) -> list[RawListing]:
        """HTML fallback: parse listing cards from search results page."""
        soup = BeautifulSoup(html, "html.parser")

        # Try to find JSON data embedded in script tags
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and ("listings" in data or "results" in data):
                    items = data.get("listings", data.get("results", []))
                    return [r for r in (self._parse_item(i) for i in items) if r]
            except Exception:
                continue

        # Pure HTML parsing
        listings = []
        for card in soup.select(".listing-card, .property-card, [class*='listing']"):
            try:
                price_el = card.select_one("[class*='price']")
                units_el = card.select_one("[class*='units']")
                addr_el = card.select_one("[class*='address']")
                link = card.select_one("a[href]")

                if not price_el:
                    continue

                price_text = re.sub(r"[^\d.]", "", price_el.get_text())
                units_text = re.sub(r"[^\d]", "", units_el.get_text() if units_el else "0")

                price = float(price_text) if price_text else 0
                units = int(units_text) if units_text else 0

                if price <= 0 or units < SEARCH_CRITERIA["min_units"]:
                    continue

                href = link["href"] if link else ""
                url = href if href.startswith("http") else f"https://www.marcusmillichap.com{href}"
                prop_id = re.search(r"/(\d+)", href)

                listings.append(RawListing(
                    source="marcus_millichap",
                    external_id=prop_id.group(1) if prop_id else href,
                    url=url,
                    address=addr_el.get_text(strip=True) if addr_el else "",
                    city="",
                    state="TX",
                    zip_code="",
                    price=price,
                    units=units,
                    year_built=None,
                    gross_monthly_rent=None,
                    annual_noi=None,
                    cap_rate_listed=None,
                    price_per_unit=price / units if units > 0 else None,
                    sqft=None,
                    lot_sqft=None,
                    description="",
                    listing_date=None,
                    days_on_market=None,
                    property_class=None,
                    occupancy_rate=None,
                    raw_data={},
                ))
            except Exception:
                continue

        return listings

    def _safe_int(self, val) -> Optional[int]:
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def _safe_float(self, val) -> Optional[float]:
        try:
            if val is None:
                return None
            return float(str(val).replace("$", "").replace(",", "").replace("%", ""))
        except (ValueError, TypeError):
            return None
