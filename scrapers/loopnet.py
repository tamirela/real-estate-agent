"""
LoopNet scraper using RapidAPI (loopnet-api.p.rapidapi.com).
Step 1: Search by coordinates → get listing IDs (works on free plan)
Step 2: Fetch individual listing pages via Playwright (bypasses 403)
"""

import json
import logging
import time
from typing import Optional
from .base import BaseScraper, RawListing
from config import SEARCH_CRITERIA, API_KEYS

logger = logging.getLogger(__name__)

LOOPNET_API_HOST = "loopnet-api.p.rapidapi.com"

# DFW coordinate clusters — multiple points to cover the metro
DFW_SEARCH_POINTS = [
    {"coords": [-96.7970, 32.7767], "name": "Dallas"},
    {"coords": [-97.3308, 32.7555], "name": "Fort Worth"},
    {"coords": [-96.6989, 33.0198], "name": "Plano/Frisco"},
    {"coords": [-97.0641, 32.7357], "name": "Arlington/Grand Prairie"},
    {"coords": [-96.9389, 32.8140], "name": "Irving/Carrollton"},
    {"coords": [-96.6300, 32.9126], "name": "Garland/Mesquite"},
]

RADIUS_MILES = 15


class LoopNetScraper(BaseScraper):
    """LoopNet via RapidAPI — returns listing IDs, then fetches details via browser."""

    def __init__(self):
        super().__init__()
        self.api_key = API_KEYS.get("rapidapi", "")
        if self.api_key:
            self.session.headers.update({
                "x-rapidapi-key": self.api_key,
                "x-rapidapi-host": LOOPNET_API_HOST,
                "Content-Type": "application/json",
            })

    def scrape(self, markets: list[str]) -> list[RawListing]:
        if not self.api_key:
            logger.info("[LoopNet] No RapidAPI key — skipping")
            return []

        logger.info("[LoopNet] Fetching DFW listing IDs via RapidAPI...")
        listing_ids = self._get_all_listing_ids()
        logger.info(f"[LoopNet] Got {len(listing_ids)} listing IDs — fetching details via browser...")

        if not listing_ids:
            return []

        # Fetch details for each listing using Playwright
        listings = self._fetch_details_browser(listing_ids)
        logger.info(f"[LoopNet] Successfully parsed {len(listings)} listings")
        return listings

    def _get_all_listing_ids(self) -> list[str]:
        """Use RapidAPI to get listing IDs across DFW."""
        seen = set()
        all_ids = []

        for point in DFW_SEARCH_POINTS:
            try:
                time.sleep(self.delay)
                resp = self.session.post(
                    f"https://{LOOPNET_API_HOST}/loopnet/sale/searchByCoordination",
                    json={
                        "coordination": point["coords"],
                        "radius": RADIUS_MILES,
                        "page": 1,
                    },
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    listings = data.get("data", [])
                    for item in listings:
                        lid = str(item.get("listingId", ""))
                        if lid and lid not in seen:
                            seen.add(lid)
                            all_ids.append(lid)
                    logger.info(f"[LoopNet] {point['name']}: {len(listings)} IDs")
                else:
                    logger.warning(f"[LoopNet] {point['name']}: HTTP {resp.status_code}")
            except Exception as e:
                logger.error(f"[LoopNet] {point['name']} failed: {e}")

        return all_ids

    def _fetch_details_browser(self, listing_ids: list[str]) -> list[RawListing]:
        """Use Playwright to fetch individual listing pages for full details."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("[LoopNet] Playwright not available — storing IDs as stubs only")
            return self._create_stubs(listing_ids)

        listings = []
        # Limit to 50 per run to stay within free plan (100 requests total across all sources)
        ids_to_fetch = listing_ids[:50]

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()

            for i, listing_id in enumerate(ids_to_fetch):
                try:
                    listing = self._fetch_single_listing(page, listing_id)
                    if listing:
                        listings.append(listing)
                    if i % 10 == 0 and i > 0:
                        logger.info(f"[LoopNet] Processed {i}/{len(ids_to_fetch)} listings...")
                    time.sleep(1.5)  # Be polite
                except Exception as e:
                    logger.debug(f"[LoopNet] Failed {listing_id}: {e}")

            browser.close()

        return listings

    def _fetch_single_listing(self, page, listing_id: str) -> Optional[RawListing]:
        """Fetch and parse a single LoopNet listing page."""
        url = f"https://www.loopnet.com/listing/{listing_id}/"

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(1)

            # Extract embedded JSON data
            data = page.evaluate("""() => {
                // Look for JSON-LD
                const ld = document.querySelector('script[type="application/ld+json"]');
                if (ld) {
                    try { return { type: 'ld', data: JSON.parse(ld.textContent) }; } catch(e) {}
                }
                // Look for app state
                for (let script of document.querySelectorAll('script')) {
                    const t = script.textContent || '';
                    if (t.includes('askingPrice') || t.includes('listingPrice')) {
                        const m = t.match(/"(?:askingPrice|listingPrice)"\s*:\s*(\d+)/);
                        if (m) return { type: 'inline', price: m[1], html: document.body.innerText.slice(0, 3000) };
                    }
                }
                return { type: 'html', html: document.body.innerText.slice(0, 3000) };
            }""")

            return self._parse_page_data(listing_id, url, data)

        except Exception as e:
            logger.debug(f"[LoopNet] Page fetch error for {listing_id}: {e}")
            return None

    def _parse_page_data(self, listing_id: str, url: str, data: dict) -> Optional[RawListing]:
        import re

        if not data:
            return None

        dtype = data.get("type", "")

        if dtype == "ld":
            ld = data.get("data", {})
            price = self._safe_float(ld.get("offers", {}).get("price") or ld.get("price"))
            name = ld.get("name", "")
            address_data = ld.get("address", {})
            street = address_data.get("streetAddress", "")
            city = address_data.get("addressLocality", "")
            state = address_data.get("addressRegion", "TX")
            zip_code = address_data.get("postalCode", "")
            desc = ld.get("description", "")

            # Try to extract units from description
            units_match = re.search(r"(\d+)\s*(?:unit|apt|apartment)", desc, re.I)
            units = int(units_match.group(1)) if units_match else None

            if not price or not units or units < SEARCH_CRITERIA["min_units"]:
                return None

        elif dtype in ("inline", "html"):
            html_text = data.get("html", "")
            price_match = re.search(r"\$([0-9,]+(?:\.\d+)?(?:\s*[MB])?)", html_text)
            units_match = re.search(r"(\d+)\s*(?:Unit|unit|Apt|apt)", html_text)

            price = self._safe_float(
                data.get("price") or (price_match.group(1).replace(",", "") if price_match else None)
            )
            units = self._safe_int(units_match.group(1) if units_match else None)

            if not price or not units or units < SEARCH_CRITERIA["min_units"]:
                return None

            # Extract address from text
            addr_match = re.search(r"(\d+\s+\w+(?:\s+\w+){1,4}),\s*(\w+(?:\s+\w+)?),\s*([A-Z]{2})\s*(\d{5})", html_text)
            street = addr_match.group(1) if addr_match else ""
            city = addr_match.group(2) if addr_match else ""
            state = "TX"
            zip_code = addr_match.group(4) if addr_match else ""
            desc = html_text[:500]
        else:
            return None

        if price > SEARCH_CRITERIA["max_price"] or price < SEARCH_CRITERIA["min_price"]:
            return None

        return RawListing(
            source="loopnet",
            external_id=listing_id,
            url=url,
            address=street,
            city=city,
            state=state,
            zip_code=zip_code,
            price=price,
            units=units,
            year_built=None,
            gross_monthly_rent=None,
            annual_noi=None,
            cap_rate_listed=None,
            price_per_unit=price / units if units > 0 else None,
            sqft=None,
            lot_sqft=None,
            description=desc,
            listing_date=None,
            days_on_market=None,
            property_class=None,
            occupancy_rate=None,
            raw_data=data,
        )

    def _create_stubs(self, listing_ids: list[str]) -> list[RawListing]:
        """When Playwright isn't available, store minimal stubs so we can track IDs."""
        return []

    def _safe_int(self, val) -> Optional[int]:
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def _safe_float(self, val) -> Optional[float]:
        try:
            if val is None:
                return None
            return float(str(val).replace("$", "").replace(",", "").replace("M", "000000").replace("B", "000000000"))
        except (ValueError, TypeError):
            return None
