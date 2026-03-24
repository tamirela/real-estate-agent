"""
Zillow scraper — direct search via cloudscraper.
The RapidAPI zillow-com1 endpoint (/propertySearch) was removed in early 2026.
This scraper now hits Zillow's search pages directly using cloudscraper (which
handles PerimeterX/Cloudflare challenges) and extracts the __NEXT_DATA__ JSON
embedded in each results page.

Covers residential multifamily (2-20+ units) across DFW.
"""

import json
import logging
import re
import time
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA

logger = logging.getLogger(__name__)

# Zillow city slugs and map bounds for DFW sub-markets
DFW_SEARCHES = [
    {"slug": "dallas-tx", "label": "Dallas", "north": 33.02, "south": 32.62, "east": -96.46, "west": -97.00},
    {"slug": "fort-worth-tx", "label": "Fort Worth", "north": 32.95, "south": 32.55, "east": -97.10, "west": -97.60},
    {"slug": "plano-tx", "label": "Plano", "north": 33.10, "south": 32.95, "east": -96.60, "west": -96.85},
    {"slug": "arlington-tx", "label": "Arlington", "north": 32.82, "south": 32.62, "east": -97.00, "west": -97.30},
    {"slug": "irving-tx", "label": "Irving", "north": 32.95, "south": 32.80, "east": -96.88, "west": -97.05},
    {"slug": "garland-tx", "label": "Garland", "north": 32.98, "south": 32.85, "east": -96.55, "west": -96.70},
    {"slug": "mesquite-tx", "label": "Mesquite", "north": 32.82, "south": 32.72, "east": -96.55, "west": -96.65},
    {"slug": "grand-prairie-tx", "label": "Grand Prairie", "north": 32.78, "south": 32.65, "east": -96.95, "west": -97.05},
]

# Max pages to fetch per city (Zillow caps at 20 pages, ~40 per page)
MAX_PAGES = 5
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)


class ZillowScraper(BaseScraper):
    """Zillow direct scraper — multifamily listings via search page parsing."""

    def scrape(self, markets: list[str]) -> list[RawListing]:
        logger.info("[Zillow] Searching DFW multifamily (direct scrape)...")
        listings: list[RawListing] = []
        seen: set[str] = set()

        for area in DFW_SEARCHES:
            try:
                results = self._search_area(area)
                for r in results:
                    if r.external_id not in seen:
                        seen.add(r.external_id)
                        listings.append(r)
            except Exception as e:
                logger.error(f"[Zillow] Search failed for {area['label']}: {e}")

        logger.info(f"[Zillow] Found {len(listings)} unique listings")
        return listings

    # ── internal helpers ──────────────────────────────────────────────

    def _build_search_state(self, area: dict, page: int = 1) -> dict:
        """Build the Zillow searchQueryState JSON for a given area and page."""
        state: dict = {
            "isMapVisible": False,
            "mapBounds": {
                "north": area["north"],
                "south": area["south"],
                "east": area["east"],
                "west": area["west"],
            },
            "filterState": {
                "price": {
                    "min": SEARCH_CRITERIA["min_price"],
                    "max": SEARCH_CRITERIA["max_price"],
                },
                "sf": {"value": False},     # no single-family
                "con": {"value": False},    # no condos
                "land": {"value": False},   # no land
                "tow": {"value": False},    # no townhouse
                "manu": {"value": False},   # no manufactured
                "apa": {"value": False},    # no individual apartments
                "apco": {"value": False},   # no apartment-complex listings
                "multi": {"value": True},   # YES multi-family
            },
            "isListVisible": True,
        }
        if page > 1:
            state["pagination"] = {"currentPage": page}
        else:
            state["pagination"] = {}
        return state

    def _search_area(self, area: dict) -> list[RawListing]:
        """Fetch all pages for one DFW sub-market."""
        all_items: list[RawListing] = []
        for page in range(1, MAX_PAGES + 1):
            search_state = self._build_search_state(area, page)
            page_suffix = f"{page}_p/" if page > 1 else ""
            url = f"https://www.zillow.com/{area['slug']}/duplex_triplex_fourplex/{page_suffix}"
            params = {"searchQueryState": json.dumps(search_state)}

            response = self.get(url, params=params)
            if not response:
                logger.warning(f"[Zillow] No response for {area['label']} page {page}")
                break

            items = self._extract_listings(response.text)
            if not items:
                break  # no more results

            parsed = [r for r in (self._parse(item) for item in items) if r]
            all_items.extend(parsed)
            logger.debug(f"[Zillow] {area['label']} p{page}: {len(parsed)} parsed of {len(items)} raw")

            if len(items) < 10:
                break  # last page

            time.sleep(1)  # be polite between pages

        return all_items

    def _extract_listings(self, html: str) -> list[dict]:
        """Pull listing dicts from Zillow's embedded __NEXT_DATA__ JSON."""
        match = NEXT_DATA_RE.search(html)
        if not match:
            logger.debug("[Zillow] No __NEXT_DATA__ found in response")
            return []

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.debug("[Zillow] Failed to parse __NEXT_DATA__ JSON")
            return []

        try:
            sps = data["props"]["pageProps"]["searchPageState"]
            cat1 = sps.get("cat1", {})
            return cat1.get("searchResults", {}).get("listResults", [])
        except (KeyError, TypeError):
            return []

    def _parse(self, item: dict) -> Optional[RawListing]:
        """Convert a Zillow listResult dict into a RawListing."""
        try:
            hdp = item.get("hdpData", {}).get("homeInfo", {})
            home_type = hdp.get("homeType", "")
            if home_type not in ("MULTI_FAMILY", "APARTMENT"):
                return None

            price = self._safe_float(item.get("unformattedPrice") or hdp.get("price"))
            if not price:
                return None
            if price < SEARCH_CRITERIA["min_price"] or price > SEARCH_CRITERIA["max_price"]:
                return None

            # Zillow doesn't expose unit count directly for multi-family.
            # Use bedrooms as a rough proxy (e.g. a fourplex with 2BR each = 8 beds).
            # We keep anything with enough beds to plausibly meet min_units,
            # but mark units=beds so downstream scoring can refine.
            beds = self._safe_int(item.get("beds") or hdp.get("bedrooms")) or 0
            # For multi-family, beds >= min_units is a reasonable heuristic:
            # a 10-unit building almost always has >= 10 bedrooms total.
            if beds < SEARCH_CRITERIA.get("min_units", 2):
                return None

            zpid = str(item.get("zpid", hdp.get("zpid", "")))
            address = item.get("addressStreet", hdp.get("streetAddress", ""))
            city = item.get("addressCity", hdp.get("city", ""))
            state = item.get("addressState", hdp.get("state", "TX"))
            zip_code = str(item.get("addressZipcode", hdp.get("zipcode", "")))
            detail_url = item.get("detailUrl", f"https://www.zillow.com/homedetails/{zpid}_zpid/")

            rent_zestimate = self._safe_float(hdp.get("rentZestimate"))
            sqft = self._safe_int(item.get("area") or hdp.get("livingArea"))
            lot_sqft = self._safe_int(hdp.get("lotAreaValue"))
            days_on = self._safe_int(hdp.get("daysOnZillow"))

            return RawListing(
                source="zillow",
                external_id=zpid,
                url=detail_url,
                address=address,
                city=city,
                state=state,
                zip_code=zip_code,
                price=price,
                units=beds,
                year_built=self._safe_int(hdp.get("yearBuilt")),
                gross_monthly_rent=rent_zestimate,
                annual_noi=None,
                cap_rate_listed=None,
                price_per_unit=price / beds if beds > 0 else None,
                sqft=sqft,
                lot_sqft=lot_sqft,
                description=item.get("flexFieldText", ""),
                listing_date=None,
                days_on_market=days_on,
                property_class=None,
                occupancy_rate=None,
                raw_data=item,
            )
        except Exception as e:
            logger.debug(f"[Zillow] Parse error: {e}")
            return None

    # ── type-safe helpers ─────────────────────────────────────────────

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
