"""
Redfin scraper — completely free, no API key required.
Redfin's API is public and returns JSON data.
Uses bounding-box (poly) search across DFW sub-regions for multifamily.

NOTE: Redfin is a residential platform — it lists smaller multifamily
(duplexes, triplexes, quads, small apartment buildings) that commercial
brokerages don't carry. We intentionally use a low min-units threshold
(2) here and let the downstream pipeline apply stricter criteria.
"""

import json
import logging
import re
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA

logger = logging.getLogger(__name__)

# Redfin's internal GIS search API
REDFIN_API = "https://www.redfin.com/stingray/api/gis"

# DFW sub-region bounding boxes (lon lat pairs, counter-clockwise polygon).
# Splitting into sub-regions maximises coverage since the API caps at ~350 per query.
DFW_BBOXES = [
    {
        "name": "Dallas-Central",
        "poly": "-96.92 32.72,-96.92 32.85,-96.72 32.85,-96.72 32.72,-96.92 32.72",
    },
    {
        "name": "Dallas-South",
        "poly": "-96.95 32.60,-96.95 32.72,-96.65 32.72,-96.65 32.60,-96.95 32.60",
    },
    {
        "name": "Dallas-North",
        "poly": "-96.90 32.85,-96.90 33.00,-96.65 33.00,-96.65 32.85,-96.90 32.85",
    },
    {
        "name": "Dallas-East",
        "poly": "-96.72 32.70,-96.72 32.90,-96.50 32.90,-96.50 32.70,-96.72 32.70",
    },
    {
        "name": "Fort Worth",
        "poly": "-97.50 32.60,-97.50 32.85,-97.20 32.85,-97.20 32.60,-97.50 32.60",
    },
    {
        "name": "Arlington-GP",
        "poly": "-97.20 32.60,-97.20 32.80,-96.95 32.80,-96.95 32.60,-97.20 32.60",
    },
    {
        "name": "Plano-Frisco-McKinney",
        "poly": "-96.85 33.00,-96.85 33.25,-96.60 33.25,-96.60 33.00,-96.85 33.00",
    },
    {
        "name": "Denton-Carrollton",
        "poly": "-97.20 32.95,-97.20 33.25,-96.85 33.25,-96.85 32.95,-97.20 32.95",
    },
    {
        "name": "Irving-Garland-Richardson",
        "poly": "-97.00 32.80,-97.00 32.98,-96.60 32.98,-96.60 32.80,-97.00 32.80",
    },
    {
        "name": "Mesquite-SE",
        "poly": "-96.65 32.55,-96.65 32.75,-96.40 32.75,-96.40 32.55,-96.65 32.55",
    },
]

REDFIN_HEADERS = {
    "Accept": "*/*",
    "Referer": "https://www.redfin.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# Redfin uiPropertyType 4 = multifamily (duplexes through apartment buildings)
MULTIFAMILY_UI_TYPE = 4

# Redfin covers residential multifamily — use a permissive minimum unit count
# so we capture duplexes through small apartment buildings. The main pipeline
# can apply SEARCH_CRITERIA["min_units"] downstream if stricter filtering is wanted.
REDFIN_MIN_UNITS = 2


class RedfinScraper(BaseScraper):
    """Free Redfin scraper for DFW multifamily listings."""

    def __init__(self):
        super().__init__()
        self.session.headers.update(REDFIN_HEADERS)

    def scrape(self, markets: list[str]) -> list[RawListing]:
        logger.info("[Redfin] Searching DFW multifamily (free, no key needed)...")
        listings = []
        seen = set()

        for bbox in DFW_BBOXES:
            try:
                results = self._search_bbox(bbox)
                new = 0
                for r in results:
                    if r.external_id not in seen:
                        seen.add(r.external_id)
                        listings.append(r)
                        new += 1
                logger.info(f"[Redfin]   {bbox['name']}: {new} new listings")
            except Exception as e:
                logger.error(f"[Redfin] {bbox['name']} failed: {e}")

        logger.info(f"[Redfin] Total: {len(listings)} unique multifamily listings")
        return listings

    def _search_bbox(self, bbox: dict) -> list[RawListing]:
        params = {
            "al": 1,
            "market": "dallas",
            "num_homes": 350,
            "ord": "redfin-recommended-asc",
            "page_number": 1,
            "poly": bbox["poly"],
            "status": 9,                 # For sale
            "v": 8,
        }

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
            logger.warning(f"[Redfin] Could not parse JSON for {bbox['name']}")
            return []

        homes = data.get("payload", {}).get("homes", [])

        results = []
        for home in homes:
            # Client-side filter: only multifamily
            if home.get("uiPropertyType") != MULTIFAMILY_UI_TYPE:
                continue
            listing = self._parse(home)
            if listing:
                results.append(listing)
        return results

    def _parse(self, home: dict) -> Optional[RawListing]:
        """Parse a single home dict from Redfin GIS API response.

        Actual API fields (flat structure, NOT nested in homeData):
          price: {value: int, level: int}
          streetLine: {value: str, level: int}
          city: str
          state: str
          zip: str
          beds: int
          sqFt: {value: int, level: int}
          yearBuilt: {value: int, level: int}
          dom: {value: int, level: int}
          propertyId: int
          listingId: int
          mlsId: {label: str, value: str}
          url: str (relative path)
          listingRemarks: str
          propertyType: int (4=duplex/triplex, 5=apartment)
        """
        try:
            # --- Price ---
            price_raw = home.get("price")
            if isinstance(price_raw, dict):
                price = self._safe_float(price_raw.get("value"))
            else:
                price = self._safe_float(price_raw)
            if not price or price <= 0:
                return None

            # --- Units ---
            # Redfin does not expose a dedicated "units" field.
            # Strategy: extract from listing remarks first, then infer from beds.
            units = self._extract_units_from_remarks(home.get("listingRemarks", ""))
            if not units:
                # propertyType 5 = apartment complex, usually larger
                if home.get("propertyType") == 5:
                    units = self._safe_int(home.get("beds")) or 2
                else:
                    # For duplexes (propertyType 4), beds is total across units
                    beds = self._safe_int(home.get("beds"))
                    units = max(beds // 2, 2) if beds and beds >= 2 else 2

            if units < REDFIN_MIN_UNITS:
                return None

            # --- IDs ---
            mls_id = home.get("mlsId", {})
            if isinstance(mls_id, dict):
                mls_id = mls_id.get("value", "")
            listing_id = str(home.get("listingId") or home.get("propertyId") or mls_id or "")

            # --- Address ---
            street_raw = home.get("streetLine")
            if isinstance(street_raw, dict):
                address = street_raw.get("value", "")
            else:
                address = street_raw or ""

            city = home.get("city", "")
            state = home.get("state", "TX")
            zip_code = str(home.get("zip", home.get("postalCode", {}).get("value", "") if isinstance(home.get("postalCode"), dict) else home.get("postalCode", "")))

            # --- URL ---
            url_path = home.get("url", "")
            url = f"https://www.redfin.com{url_path}" if url_path and not url_path.startswith("http") else url_path

            # --- sqft ---
            sqft_raw = home.get("sqFt")
            if isinstance(sqft_raw, dict):
                sqft = self._safe_int(sqft_raw.get("value"))
            else:
                sqft = self._safe_int(sqft_raw)

            # --- Year built ---
            yb_raw = home.get("yearBuilt")
            if isinstance(yb_raw, dict):
                year_built = self._safe_int(yb_raw.get("value"))
            else:
                year_built = self._safe_int(yb_raw)

            # --- DOM ---
            dom_raw = home.get("dom")
            if isinstance(dom_raw, dict):
                dom = self._safe_int(dom_raw.get("value"))
            else:
                dom = self._safe_int(dom_raw)

            # --- Lot size ---
            lot_raw = home.get("lotSize")
            if isinstance(lot_raw, dict):
                lot_sqft = self._safe_int(lot_raw.get("value"))
            else:
                lot_sqft = self._safe_int(lot_raw)

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
                year_built=year_built,
                gross_monthly_rent=None,
                annual_noi=None,
                cap_rate_listed=None,
                price_per_unit=price / units if units > 0 else None,
                sqft=sqft,
                lot_sqft=lot_sqft,
                description=home.get("listingRemarks", ""),
                listing_date=None,
                days_on_market=dom,
                property_class=None,
                occupancy_rate=None,
                raw_data=home,
            )
        except Exception as e:
            logger.debug(f"[Redfin] Parse error: {e}")
            return None

    def _extract_units_from_remarks(self, remarks: str) -> Optional[int]:
        """Try to pull a unit count from listing description text."""
        if not remarks:
            return None
        text = remarks.lower()
        # Match patterns like "5-unit", "12 unit", "8 units"
        m = re.search(r'(\d+)\s*-?\s*units?', text)
        if m:
            return int(m.group(1))
        # Match "triplex" "duplex" "fourplex" "quadplex"
        word_map = {"duplex": 2, "triplex": 3, "fourplex": 4, "quadplex": 4,
                     "quad-plex": 4, "sixplex": 6, "six-plex": 6, "eightplex": 8}
        for word, count in word_map.items():
            if word in text:
                return count
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
