"""
RentCast API scraper — most reliable source.
Provides for-sale listings with rental income data.
Free tier: 50 requests/month. Paid: $35/month.
Sign up at: https://rentcast.io/

Even without an API key, RentCast has a public endpoint we can use
to get property data for zip codes in DFW.
"""

import logging
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA, API_KEYS

logger = logging.getLogger(__name__)

# DFW zip codes covering multifamily-heavy areas
DFW_ZIP_CODES = [
    # Dallas core
    "75201", "75202", "75203", "75204", "75205", "75206", "75207", "75208",
    "75209", "75210", "75211", "75212", "75214", "75215", "75216", "75217",
    "75218", "75219", "75220", "75223", "75224", "75225", "75226", "75227",
    "75228", "75229", "75230", "75231", "75232", "75233", "75235", "75237",
    "75238", "75240", "75241", "75243", "75244", "75246", "75247", "75248",
    # Fort Worth
    "76101", "76102", "76103", "76104", "76105", "76106", "76107", "76108",
    "76109", "76110", "76111", "76112", "76114", "76115", "76116", "76117",
    "76118", "76119", "76120", "76123", "76132", "76133", "76134", "76135",
    # Suburbs
    "75007", "75010", "75019", "75024", "75025", "75034", "75035",  # Frisco/Plano
    "75050", "75051", "75052", "75054",  # Grand Prairie
    "75060", "75061", "75062", "75063",  # Irving
    "75067", "75068", "75077",  # Lewisville/Denton
    "75080", "75081", "75082",  # Richardson
    "75087", "75088", "75089",  # Rowlett/Garland
    "75141", "75149", "75150",  # Mesquite
    "76006", "76010", "76011", "76012", "76013", "76014", "76015", "76016",  # Arlington
    "75006", "75007", "75010", "75011",  # Carrollton
    "75040", "75041", "75042", "75043", "75044",  # Garland
]

RENTCAST_BASE = "https://api.rentcast.io/v1"


class RentCastScraper(BaseScraper):
    """
    RentCast API — reliable property data with rental income estimates.
    Works with or without an API key (limited without key).
    """

    def __init__(self):
        super().__init__()
        self.api_key = API_KEYS.get("rentcast", "")
        if self.api_key:
            self.session.headers["X-Api-Key"] = self.api_key

    def scrape(self, markets: list[str]) -> list[RawListing]:
        if not self.api_key:
            logger.info("[RentCast] No API key — skipping. Add RENTCAST_API_KEY to .env for better data.")
            return []

        logger.info("[RentCast] Searching DFW multifamily for-sale listings...")
        listings = []

        # Search by zip codes (more granular results)
        seen_ids = set()
        for zip_code in DFW_ZIP_CODES[:30]:  # Limit to 30 zips to stay within API limits
            try:
                zips_listings = self._search_zip(zip_code)
                for l in zips_listings:
                    if l.external_id not in seen_ids:
                        seen_ids.add(l.external_id)
                        listings.append(l)
            except Exception as e:
                logger.debug(f"[RentCast] Zip {zip_code} error: {e}")

        logger.info(f"[RentCast] Found {len(listings)} unique listings")
        return listings

    def _search_zip(self, zip_code: str) -> list[RawListing]:
        """Fetch for-sale multifamily listings for a zip code."""
        params = {
            "zipCode": zip_code,
            "propertyType": "Apartment",  # RentCast type for multifamily
            "status": "Active",
            "limit": 500,
        }

        response = self.get(f"{RENTCAST_BASE}/listings/sale", params=params)
        if not response:
            return []

        try:
            data = response.json()
        except Exception:
            return []

        items = data if isinstance(data, list) else data.get("listings", data.get("data", []))
        results = []
        for item in items:
            listing = self._parse(item)
            if listing:
                results.append(listing)
        return results

    def _parse(self, item: dict) -> Optional[RawListing]:
        try:
            price = self._safe_float(item.get("price", item.get("listPrice")))
            units = self._safe_int(item.get("units", item.get("bedrooms")))  # bedrooms as proxy for small MF

            if not price or price <= 0:
                return None
            if price > SEARCH_CRITERIA["max_price"] or price < SEARCH_CRITERIA["min_price"]:
                return None
            if not units or units < SEARCH_CRITERIA["min_units"]:
                return None

            prop_id = str(item.get("id", item.get("propertyId", "")))
            address = item.get("formattedAddress", item.get("address", ""))

            # RentCast gives us rent estimate
            rent_estimate = self._safe_float(item.get("rentEstimate", item.get("estimatedRent")))
            gross_monthly = rent_estimate * units if rent_estimate and units else None

            return RawListing(
                source="rentcast",
                external_id=prop_id,
                url=f"https://app.rentcast.io/app?address={address.replace(' ', '+')}",
                address=address,
                city=item.get("city", ""),
                state=item.get("state", "TX"),
                zip_code=str(item.get("zipCode", item.get("zip", ""))),
                price=price,
                units=units,
                year_built=self._safe_int(item.get("yearBuilt")),
                gross_monthly_rent=gross_monthly,
                annual_noi=None,
                cap_rate_listed=None,
                price_per_unit=price / units if units > 0 else None,
                sqft=self._safe_int(item.get("squareFootage", item.get("sqft"))),
                lot_sqft=self._safe_int(item.get("lotSize")),
                description=item.get("description", ""),
                listing_date=item.get("listedDate", item.get("listingDate", "")),
                days_on_market=self._safe_int(item.get("daysOnMarket")),
                property_class=None,
                occupancy_rate=None,
                raw_data=item,
            )
        except Exception as e:
            logger.debug(f"[RentCast] Parse error: {e}")
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
