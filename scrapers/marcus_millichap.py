"""
Marcus & Millichap listing scraper.
MM is one of the top multifamily brokers in DFW - great source for off-market and listed deals.

Uses the Sitecore content search API at /api/contentsearch/properties (POST).
Facets:
  - propertytype: "Apartments" (parent category containing Multifamily)
  - StateProvinceName: "Texas" (state-level filter)
  - City + StateProvince: per-city filter (from locationAutoComplete)
Tile HTML in each result contains the actual listing data (price, units, location, etc.).
"""

import re
import time
import logging
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA

logger = logging.getLogger(__name__)

# DFW metro area cities (lowercase for matching)
DFW_CITIES = {
    "dallas", "fort worth", "plano", "irving", "arlington", "garland",
    "frisco", "mckinney", "grand prairie", "mesquite", "denton",
    "carrollton", "richardson", "lewisville", "allen", "flower mound",
    "rowlett", "wylie", "mansfield", "desoto", "cedar hill", "duncanville",
    "lancaster", "coppell", "farmers branch", "addison", "sachse", "murphy",
    "euless", "bedford", "hurst", "grapevine", "colleyville", "keller",
    "north richland hills", "watauga", "haltom city", "benbrook",
    "burleson", "cleburne", "weatherford", "mineral wells", "azle",
    "white settlement", "lake worth", "river oaks", "sansom park",
    "forest hill", "kennedale", "crowley", "joshua", "midlothian",
    "waxahachie", "ennis", "corsicana", "terrell", "rockwall",
    "greenville", "forney", "heath", "royse city", "fate",
    "prosper", "celina", "anna", "princeton", "farmersville",
    "the colony", "little elm", "aubrey", "sanger", "corinth",
    "highland village", "lake dallas", "shady shores",
    "southlake", "trophy club", "roanoke", "haslet", "justin",
    "argyle", "bartonville", "copper canyon", "double oak",
    "red oak", "glenn heights", "hutchins", "wilmer", "seagoville",
    "balch springs", "sunnyvale", "combine", "heartland",
}


class MarcusMillichapScraper(BaseScraper):
    """Scrapes Marcus & Millichap for multifamily listings in DFW."""

    API_URL = "https://www.marcusmillichap.com/api/contentsearch/properties"
    BASE_URL = "https://www.marcusmillichap.com"

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
        """Fetch all Texas apartment listings and filter for DFW metro."""
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/properties",
        })

        all_properties = []
        page = 1
        max_pages = 10  # safety limit

        while page <= max_pages:
            payload = {
                "pageNumber": page,
                "pageSize": 200,
                "sortOrder": "DESC",
                "indexFieldName": "orderdate",
                "facets": [
                    {"fieldName": "propertytype", "fieldValue": "Apartments", "subFieldName": ""},
                    {"fieldName": "StateProvinceName", "fieldValue": "Texas", "subFieldName": ""},
                ],
                "rangeFacets": [],
                "geoFacet": None,
                "savedSearchId": 0,
                "allowedFacets": [
                    "propertytype", "location", "advisors",
                    "listingprice", "caprate",
                ],
            }

            time.sleep(self.delay)
            try:
                response = self.session.post(
                    self.API_URL, json=payload, timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.warning(f"[MM] API request failed (page {page}): {e}")
                break

            results = data.get("Results", {})
            properties = results.get("Properties", [])
            total_count = results.get("TotalCount", 0)
            num_pages = results.get("NumberOfPages", 0)

            if not properties:
                break

            all_properties.extend(properties)
            logger.debug(
                f"[MM] Page {page}: got {len(properties)} properties "
                f"({total_count} total, {num_pages} pages)"
            )

            if page >= num_pages:
                break
            page += 1

        logger.info(
            f"[MM] Fetched {len(all_properties)} Texas apartment listings, "
            f"filtering for DFW metro..."
        )

        # Parse each property tile and filter for DFW
        listings = []
        for prop in all_properties:
            listing = self._parse_tile(prop)
            if listing is not None:
                listings.append(listing)

        return listings

    def _parse_tile(self, prop: dict) -> Optional[RawListing]:
        """Parse a property from the API response. Data is in the Tile HTML."""
        try:
            tile = prop.get("Tile", "")
            deal_id = str(prop.get("DealId", ""))

            # Extract location from tile HTML
            loc_match = re.search(r'mm-location["\x27]>\s*(.*?)\s*<', tile)
            if not loc_match:
                return None

            location_text = loc_match.group(1).strip()
            parts = [p.strip() for p in location_text.split(",")]
            city = parts[0] if parts else ""
            state = parts[1] if len(parts) > 1 else "TX"

            # Filter for DFW metro area
            if city.lower() not in DFW_CITIES:
                return None

            # Extract price
            price_match = re.search(r'Listing Price:\s*\$?([\d,]+)', tile)
            if price_match:
                price = float(price_match.group(1).replace(",", ""))
            else:
                # "Request For Offer" or missing price - still include it
                price = 0.0

            # Filter by price range if price is known
            if price > 0 and price > SEARCH_CRITERIA["max_price"]:
                return None

            # Extract units
            units_match = re.search(r'Number of Units:\s*(\d+)', tile)
            units = int(units_match.group(1)) if units_match else 0

            # Filter by minimum units
            if units > 0 and units < SEARCH_CRITERIA["min_units"]:
                return None

            # Extract cap rate
            cap_match = re.search(r'Cap Rate:\s*([\d.]+)%', tile)
            cap_rate = float(cap_match.group(1)) if cap_match else None

            # Extract property name
            name_match = re.search(r'<h2>(.*?)</h2>', tile)
            name = name_match.group(1).strip() if name_match else ""

            # Extract URL
            url_match = re.search(r'href=["\x27](/properties/[^"\x27]+)["\x27]', tile)
            url_path = url_match.group(1) if url_match else f"/properties/{deal_id}"
            url = f"{self.BASE_URL}{url_path}"

            # Extract property subtype from tile
            subtype_match = re.search(r'<h3>(.*?)</h3>', tile)
            subtype = subtype_match.group(1).strip() if subtype_match else ""

            # Check for new listing / price reduction flags
            is_new = "mm-feature-new-listing" in tile
            is_reduced = "mm-feature-price-reduction" in tile

            description_parts = [name]
            if subtype:
                description_parts.append(f"Type: {subtype}")
            if is_new:
                description_parts.append("NEW LISTING")
            if is_reduced:
                description_parts.append("PRICE REDUCTION")

            return RawListing(
                source="marcus_millichap",
                external_id=deal_id,
                url=url,
                address=name,  # MM uses property names, not street addresses
                city=city,
                state=state,
                zip_code="",
                price=price,
                units=units,
                year_built=None,
                gross_monthly_rent=None,
                annual_noi=None,
                cap_rate_listed=cap_rate,
                price_per_unit=price / units if price > 0 and units > 0 else None,
                sqft=None,
                lot_sqft=None,
                description=" | ".join(description_parts),
                listing_date=None,
                days_on_market=None,
                property_class=None,
                occupancy_rate=None,
                raw_data={
                    "deal_id": deal_id,
                    "property_id": prop.get("PropertyId"),
                    "property_type": prop.get("PropertyType"),
                    "property_sub_type_id": prop.get("PropertySubTypeId"),
                    "newly_listed": prop.get("NewlyListed", False) or is_new,
                    "newly_reduced": prop.get("NewlyReduced", False) or is_reduced,
                    "latitude": prop.get("Latitude"),
                    "longitude": prop.get("Longitude"),
                },
            )
        except Exception as e:
            logger.debug(f"[MM] Tile parse error: {e}")
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
            return float(str(val).replace("$", "").replace(",", "").replace("%", ""))
        except (ValueError, TypeError):
            return None
