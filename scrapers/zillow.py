"""
Zillow scraper via RapidAPI.
Uses the unofficial Zillow API available through RapidAPI.
Cost: ~$10/month for the basic plan (500 requests/month).
Sign up: https://rapidapi.com/apimaker/api/zillow-com1

Covers residential multifamily (2-20 units) and some larger complexes.
"""

import logging
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA, API_KEYS

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "zillow-com1.p.rapidapi.com"
RAPIDAPI_BASE = f"https://{RAPIDAPI_HOST}"

# DFW search coordinates
DFW_SEARCHES = [
    {"location": "Dallas, TX", "ne_lat": 33.0, "ne_lng": -96.5, "sw_lat": 32.6, "sw_lng": -97.0},
    {"location": "Fort Worth, TX", "ne_lat": 32.9, "ne_lng": -97.1, "sw_lat": 32.5, "sw_lng": -97.6},
    {"location": "Plano, TX", "ne_lat": 33.1, "ne_lng": -96.6, "sw_lat": 32.9, "sw_lng": -96.9},
    {"location": "Arlington, TX", "ne_lat": 32.8, "ne_lng": -97.0, "sw_lat": 32.6, "sw_lng": -97.3},
]


class ZillowScraper(BaseScraper):
    """Zillow via RapidAPI — good for smaller multifamily (2-20 units)."""

    def __init__(self):
        super().__init__()
        self.api_key = API_KEYS.get("rapidapi", "")
        if self.api_key:
            self.session.headers.update({
                "X-RapidAPI-Key": self.api_key,
                "X-RapidAPI-Host": RAPIDAPI_HOST,
            })

    def scrape(self, markets: list[str]) -> list[RawListing]:
        if not self.api_key:
            logger.info("[Zillow] No RapidAPI key — skipping. Add RAPIDAPI_KEY to .env")
            return []

        logger.info("[Zillow/RapidAPI] Searching DFW multifamily...")
        listings = []
        seen = set()

        for search in DFW_SEARCHES:
            try:
                results = self._search(search)
                for r in results:
                    if r.external_id not in seen:
                        seen.add(r.external_id)
                        listings.append(r)
            except Exception as e:
                logger.error(f"[Zillow] Search failed for {search['location']}: {e}")

        logger.info(f"[Zillow] Found {len(listings)} listings")
        return listings

    def _search(self, area: dict) -> list[RawListing]:
        params = {
            "location": area["location"],
            "home_type": "MultiFamily",
            "status_type": "ForSale",
            "minPrice": SEARCH_CRITERIA["min_price"],
            "maxPrice": SEARCH_CRITERIA["max_price"],
            "sort": "newest",
        }

        response = self.get(f"{RAPIDAPI_BASE}/propertySearch", params=params)
        if not response:
            return []

        try:
            data = response.json()
        except Exception:
            return []

        props = data.get("props", data.get("results", data.get("data", [])))
        return [r for r in (self._parse(p) for p in props) if r]

    def _parse(self, item: dict) -> Optional[RawListing]:
        try:
            price = self._safe_float(item.get("price", item.get("listPrice")))
            if not price or price > SEARCH_CRITERIA["max_price"]:
                return None

            # Zillow doesn't always give unit count for apartment buildings
            # Use beds as proxy or default to 0 (will be filtered later if under min)
            units = self._safe_int(item.get("units", item.get("beds")))
            if not units or units < SEARCH_CRITERIA["min_units"]:
                return None

            zpid = str(item.get("zpid", item.get("id", "")))
            address = item.get("address", item.get("streetAddress", ""))
            city = item.get("city", "")
            state = item.get("state", "TX")
            zip_code = str(item.get("zipcode", item.get("zip", "")))

            return RawListing(
                source="zillow",
                external_id=zpid,
                url=item.get("detailUrl", f"https://www.zillow.com/homedetails/{zpid}_zpid/"),
                address=address,
                city=city,
                state=state,
                zip_code=zip_code,
                price=price,
                units=units,
                year_built=self._safe_int(item.get("yearBuilt")),
                gross_monthly_rent=self._safe_float(item.get("rentZestimate")),
                annual_noi=None,
                cap_rate_listed=None,
                price_per_unit=price / units if units > 0 else None,
                sqft=self._safe_int(item.get("livingArea", item.get("sqft"))),
                lot_sqft=self._safe_int(item.get("lotAreaValue")),
                description=item.get("description", ""),
                listing_date=item.get("listingDateTimeOnZillow", ""),
                days_on_market=self._safe_int(item.get("daysOnZillow")),
                property_class=None,
                occupancy_rate=None,
                raw_data=item,
            )
        except Exception as e:
            logger.debug(f"[Zillow] Parse error: {e}")
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
