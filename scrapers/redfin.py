"""
Redfin scraper — completely free, no API key required.
Redfin's API is public and returns JSON data.
Covers residential multifamily (2-10 units) well for DFW.
"""

import json
import logging
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA

logger = logging.getLogger(__name__)

# Redfin's internal GIS search API
REDFIN_API = "https://www.redfin.com/stingray/api/gis"

# DFW region polygons (Dallas=4, Fort Worth=10975, various suburbs)
DFW_REGIONS = [
    {"region_id": "4",     "region_type": "6", "name": "Dallas"},
    {"region_id": "10975", "region_type": "6", "name": "Fort Worth"},
    {"region_id": "13368", "region_type": "6", "name": "Plano"},
    {"region_id": "13079", "region_type": "6", "name": "Arlington"},
    {"region_id": "12798", "region_type": "6", "name": "Irving"},
    {"region_id": "11695", "region_type": "6", "name": "Garland"},
    {"region_id": "17551", "region_type": "6", "name": "Frisco"},
]

REDFIN_HEADERS = {
    "Accept": "*/*",
    "Referer": "https://www.redfin.com/",
    "X-Requested-With": "XMLHttpRequest",
}


class RedfinScraper(BaseScraper):
    """Free Redfin scraper for DFW multifamily listings."""

    def __init__(self):
        super().__init__()
        self.session.headers.update(REDFIN_HEADERS)

    def scrape(self, markets: list[str]) -> list[RawListing]:
        logger.info("[Redfin] Searching DFW multifamily (free, no key needed)...")
        listings = []
        seen = set()

        for region in DFW_REGIONS:
            try:
                results = self._search_region(region)
                for r in results:
                    if r.external_id not in seen:
                        seen.add(r.external_id)
                        listings.append(r)
                logger.info(f"[Redfin]   {region['name']}: {len(results)} listings")
            except Exception as e:
                logger.error(f"[Redfin] {region['name']} failed: {e}")

        logger.info(f"[Redfin] Total: {len(listings)} unique listings")
        return listings

    def _search_region(self, region: dict) -> list[RawListing]:
        params = {
            "al": 1,
            "market": "dallas",
            "num_homes": 350,
            "ord": "redfin-recommended-asc",
            "page_number": 1,
            "region_id": region["region_id"],
            "region_type": region["region_type"],
            "sf": "1,2,3,5,6,7",       # All status filters
            "status": 9,                 # For sale
            "uipt": "5",                 # Property type 5 = multifamily
            "v": 8,
        }

        # Add price filter
        if SEARCH_CRITERIA.get("max_price"):
            params["max_price"] = SEARCH_CRITERIA["max_price"]
        if SEARCH_CRITERIA.get("min_price"):
            params["min_price"] = SEARCH_CRITERIA["min_price"]

        response = self.get(REDFIN_API, params=params)
        if not response:
            return []

        # Redfin response starts with "{}&&" to prevent JSON hijacking
        text = response.text
        if text.startswith("{}&&"):
            text = text[4:]

        try:
            data = json.loads(text)
        except Exception:
            return []

        # Navigate to homes list
        homes = (
            data.get("payload", {}).get("homes", [])
            or data.get("payload", {}).get("hotHomes", [])
            or []
        )

        results = []
        for home in homes:
            listing = self._parse(home)
            if listing:
                results.append(listing)
        return results

    def _parse(self, item: dict) -> Optional[RawListing]:
        try:
            # Redfin nests data in homeData
            home = item.get("homeData", item)
            price_info = home.get("priceInfo", {})
            price = self._safe_float(
                price_info.get("amount") or price_info.get("displayLevel") or home.get("price")
            )
            if not price or price <= 0:
                return None
            if price > SEARCH_CRITERIA["max_price"] or price < SEARCH_CRITERIA["min_price"]:
                return None

            # Unit count — Redfin uses "beds" for MF, or has a units field
            units = self._safe_int(
                home.get("beds") or home.get("units") or home.get("numUnits")
            )
            if not units or units < SEARCH_CRITERIA["min_units"]:
                return None

            listing_id = str(home.get("listingId", home.get("propertyId", home.get("mlsId", {}).get("value", ""))))
            address_info = home.get("addressInfo", {})
            address = address_info.get("formattedStreetLine", home.get("streetLine", ""))
            city = address_info.get("city", home.get("city", ""))
            state = address_info.get("state", "TX")
            zip_code = str(address_info.get("zip", home.get("zip", "")))

            url_info = home.get("url", "")
            url = f"https://www.redfin.com{url_info}" if url_info and not url_info.startswith("http") else url_info

            sqft = self._safe_int(home.get("sqFt", {}).get("value") if isinstance(home.get("sqFt"), dict) else home.get("sqFt"))

            return RawListing(
                source="redfin",
                external_id=listing_id,
                url=url,
                address=address,
                city=city,
                state=state,
                zip_code=zip_code,
                price=price,
                units=units,
                year_built=self._safe_int(home.get("yearBuilt", {}).get("value") if isinstance(home.get("yearBuilt"), dict) else home.get("yearBuilt")),
                gross_monthly_rent=None,
                annual_noi=None,
                cap_rate_listed=None,
                price_per_unit=price / units if units > 0 else None,
                sqft=sqft,
                lot_sqft=None,
                description="",
                listing_date=home.get("listingAddedDate", home.get("soldDate", "")),
                days_on_market=self._safe_int(home.get("dom", {}).get("value") if isinstance(home.get("dom"), dict) else home.get("dom")),
                property_class=None,
                occupancy_rate=None,
                raw_data=item,
            )
        except Exception as e:
            logger.debug(f"[Redfin] Parse error: {e}")
            return None

    def _safe_int(self, val) -> Optional[int]:
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def _safe_float(self, val) -> Optional[float]:
        try:
            if val is None:
                return None
            return float(str(val).replace("$", "").replace(",", ""))
        except (ValueError, TypeError):
            return None
