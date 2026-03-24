"""
Crexi.com scraper for multifamily listings.
Uses Crexi's actual REST API with proper browser-like headers.
"""

import json
import logging
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA

logger = logging.getLogger(__name__)

# Crexi's actual API endpoint (used by their web app)
CREXI_API_BASE = "https://api.crexi.com"
CREXI_SEARCH_URL = f"{CREXI_API_BASE}/assets"


class CrexiScraper(BaseScraper):
    """Scrapes Crexi.com for multifamily sale listings."""

    def __init__(self):
        super().__init__()
        # Crexi requires these headers to allow access
        self.session.headers.update({
            "Origin": "https://www.crexi.com",
            "Referer": "https://www.crexi.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        })

    def scrape(self, markets: list[str]) -> list[RawListing]:
        # Search at metro level (DFW) rather than city by city
        logger.info("[Crexi] Searching DFW metro multifamily...")
        listings = self._search_metro()
        logger.info(f"[Crexi] Found {len(listings)} listings")
        return listings

    def _search_metro(self) -> list[RawListing]:
        """Search the full DFW metro area in one call."""
        params = {
            "transactionType": "sale",
            "propertyTypes": "MultiFamily",
            "states": "TX",
            "metros": "Dallas-Fort Worth",
            "minUnits": SEARCH_CRITERIA["min_units"],
            "maxPrice": SEARCH_CRITERIA["max_price"],
            "minPrice": SEARCH_CRITERIA["min_price"],
            "take": 50,
            "skip": 0,
            "sortBy": "listedDate",
            "sortDirection": "desc",
        }

        all_listings = []
        skip = 0

        while True:
            params["skip"] = skip
            response = self.get(CREXI_SEARCH_URL, params=params)
            if not response:
                break

            try:
                data = response.json()
            except Exception:
                # Try scraping the HTML search page as fallback
                logger.info("[Crexi] JSON parse failed, trying search page fallback")
                return self._html_fallback()

            # Handle different response shapes
            results = (
                data.get("results")
                or data.get("assets")
                or data.get("data")
                or (data if isinstance(data, list) else [])
            )

            if not results:
                break

            for item in results:
                listing = self._parse_listing(item)
                if listing:
                    all_listings.append(listing)

            total = data.get("total", data.get("totalCount", len(results)))
            skip += 50
            if skip >= total or skip >= 500:
                break

        return all_listings

    def _html_fallback(self) -> list[RawListing]:
        """Scrape the Crexi search page HTML if API is blocked."""
        from bs4 import BeautifulSoup
        import re

        url = "https://www.crexi.com/properties?propertyType=MultiFamily&transactionType=sale&location=Dallas-Fort+Worth%2C+TX"
        resp = self.get(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for embedded JSON data in script tags
        for script in soup.find_all("script"):
            text = script.string or ""
            if "MultiFamily" in text and "price" in text.lower():
                match = re.search(r'(\[.*"propertyType".*\])', text, re.DOTALL)
                if match:
                    try:
                        items = json.loads(match.group(1))
                        return [r for r in (self._parse_listing(i) for i in items) if r]
                    except Exception:
                        pass

        logger.warning("[Crexi] HTML fallback also failed — site may require login")
        return []

    def _parse_listing(self, item: dict) -> Optional[RawListing]:
        try:
            price = self._extract_price(item)
            units = self._extract_units(item)

            if not price or not units:
                return None
            if price > SEARCH_CRITERIA["max_price"]:
                return None
            if units < SEARCH_CRITERIA["min_units"]:
                return None

            # Handle nested address
            addr = item.get("address", {})
            if isinstance(addr, dict):
                street = addr.get("street", addr.get("line1", ""))
                city = addr.get("city", "")
                state = addr.get("state", addr.get("stateCode", "TX"))
                zip_code = addr.get("zip", addr.get("postalCode", ""))
            else:
                street = str(addr)
                city = item.get("city", "")
                state = "TX"
                zip_code = item.get("zip", "")

            prop_id = str(item.get("id", item.get("assetId", item.get("propertyId", ""))))
            slug = item.get("slug", item.get("urlSlug", prop_id))
            url = f"https://www.crexi.com/properties/{slug}"

            return RawListing(
                source="crexi",
                external_id=prop_id,
                url=url,
                address=street,
                city=city,
                state=state,
                zip_code=str(zip_code),
                price=float(price),
                units=int(units),
                year_built=self._safe_int(item.get("yearBuilt")),
                gross_monthly_rent=self._safe_float(
                    item.get("grossMonthlyRent") or item.get("monthlyGrossRent")
                ),
                annual_noi=self._safe_float(item.get("noi") or item.get("annualNOI")),
                cap_rate_listed=self._safe_float(
                    item.get("capRate") or item.get("capRateOffered")
                ),
                price_per_unit=float(price) / int(units),
                sqft=self._safe_int(item.get("buildingSize") or item.get("totalSqFt")),
                lot_sqft=None,
                description=item.get("description", ""),
                listing_date=item.get("listedDate", item.get("listingDate", "")),
                days_on_market=self._safe_int(item.get("daysOnMarket")),
                property_class=self._infer_class(item),
                occupancy_rate=self._safe_float(
                    item.get("occupancyRate") or item.get("occupancy")
                ),
                raw_data=item,
            )
        except Exception as e:
            logger.debug(f"[Crexi] Parse error: {e}")
            return None

    def _extract_price(self, item: dict) -> Optional[float]:
        for key in ["listingPrice", "askingPrice", "price", "salePrice"]:
            val = item.get(key)
            if val:
                try:
                    return float(str(val).replace("$", "").replace(",", ""))
                except ValueError:
                    pass
        return None

    def _extract_units(self, item: dict) -> Optional[int]:
        for key in ["numberOfUnits", "units", "unitCount", "totalUnits", "numUnits"]:
            val = item.get(key)
            if val:
                try:
                    return int(val)
                except ValueError:
                    pass
        return None

    def _infer_class(self, item: dict) -> Optional[str]:
        year = self._safe_int(item.get("yearBuilt"))
        desc = (item.get("description", "") + item.get("name", "")).lower()
        for cls in ["class a", "class b", "class c"]:
            if cls in desc:
                return cls[-1].upper()
        if year:
            if year >= 2000:
                return "A"
            elif year >= 1985:
                return "B"
            else:
                return "C"
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
            s = str(val).replace("$", "").replace(",", "").replace("%", "").strip()
            f = float(s)
            if "%" in str(val) and f > 1:
                f = f / 100
            return f
        except (ValueError, TypeError):
            return None
