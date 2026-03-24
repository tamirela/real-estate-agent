"""
Crexi scraper using Playwright with authenticated login.
Strategy:
  1. Load Crexi homepage (passes Cloudflare)
  2. Login via modal → intercept JWT access_token
  3. Call Crexi REST API using browser's fetch() (has Cloudflare cookies)
"""

import json
import logging
import os
import time
from typing import Optional
from .base import RawListing
from config import SEARCH_CRITERIA

logger = logging.getLogger(__name__)

CREXI_EMAIL = os.getenv("CREXI_EMAIL", "")
CREXI_PASSWORD = os.getenv("CREXI_PASSWORD", "")


class CrexiBrowserScraper:

    def scrape(self, markets: list[str]) -> list[RawListing]:
        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import Stealth
        except ImportError:
            logger.error("[Crexi] Run: pip3 install playwright playwright-stealth && python3 -m playwright install chromium")
            return []

        if not CREXI_EMAIL or not CREXI_PASSWORD:
            logger.warning("[Crexi] No credentials in .env — skipping")
            return []

        logger.info("[Crexi] Launching browser...")
        listings = []

        with sync_playwright() as p:
            stealth = Stealth()
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            page = context.new_page()
            stealth.apply_stealth_sync(page)

            # Intercept JWT token from login response
            token_data = {}
            def on_response(resp):
                try:
                    if "api.crexi.com/token" in resp.url and resp.status == 200:
                        token_data.update(resp.json())
                        logger.info("[Crexi] Auth token captured!")
                except Exception:
                    pass
            page.on("response", on_response)

            try:
                # Step 1: Load homepage (passes Cloudflare challenge)
                logger.info("[Crexi] Loading homepage...")
                page.goto("https://www.crexi.com/", wait_until="domcontentloaded", timeout=90000)
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                time.sleep(5)

                # Step 2: Login via modal
                success = self._login(page)
                if not success:
                    logger.error("[Crexi] Login failed")
                    browser.close()
                    return []

                # Wait for login to process on the server (auth cookies set)
                time.sleep(10)

                # Verify login by checking for user initials in nav
                nav_text = page.evaluate("() => document.body.innerText.substring(0, 300)")
                if "login" in nav_text.lower() or "sign in" in nav_text.lower():
                    logger.warning("[Crexi] Login may have failed (still showing login UI)")
                else:
                    logger.info("[Crexi] Login verified (no login prompt in nav)")

                # Step 3: Trigger Angular's route guard to call /token.
                # Use page.goto() with wait_until='commit' — Angular fires the auth
                # service call immediately when navigation starts, before Cloudflare
                # can block the page. Token is captured in under 1 second.
                logger.info("[Crexi] Triggering auth token via page navigation...")
                try:
                    page.goto(
                        "https://www.crexi.com/properties?transactionType=sale&propertyType=MultiFamily",
                        wait_until="commit",
                        timeout=30000,
                    )
                except Exception:
                    pass  # Navigation will be blocked by Cloudflare — token was already captured

                # Token should arrive within a few seconds of navigation start
                for _ in range(60):
                    if token_data:
                        break
                    time.sleep(0.5)

                if not token_data:
                    logger.warning("[Crexi] Token not received after navigation trigger")
                    browser.close()
                    return []

                access_token = (
                    token_data.get("access_token")
                    or token_data.get("accessToken")
                    or token_data.get("token")
                )
                if not access_token:
                    logger.warning(f"[Crexi] No access_token in token data. Keys: {list(token_data.keys())}")
                    browser.close()
                    return []

                logger.info(f"[Crexi] Token obtained: {access_token[:20]}...")

                # Step 4: Search using browser fetch (inherits Cloudflare cookies)
                listings = self._search_with_token(page, access_token)

            except Exception as e:
                logger.error(f"[Crexi] Error: {e}")
            finally:
                browser.close()

        logger.info(f"[Crexi] Found {len(listings)} qualifying listings")
        return listings

    def _login(self, page) -> bool:
        """Open the Sign in modal and submit credentials."""
        try:
            # Click "Sign in" button to open modal
            sign_in = page.query_selector('button:has-text("Sign in"), a:has-text("Sign in")')
            if not sign_in:
                logger.error("[Crexi] No 'Sign in' button found on homepage")
                return False
            sign_in.click()
            logger.info("[Crexi] Opened login modal")
            time.sleep(3)

            # Switch from "Sign Up" tab to "Log In" tab
            page.evaluate("""() => {
                const tabs = document.querySelectorAll('[role=tab]');
                for (const t of tabs) {
                    if (t.textContent.trim().toLowerCase().includes('log in')) {
                        t.click(); break;
                    }
                }
            }""")
            time.sleep(2)

            # Disable CDK backdrop so Playwright can click the modal inputs
            page.evaluate("""() => {
                const backdrop = document.querySelector('.cdk-overlay-backdrop');
                if (backdrop) {
                    backdrop.style.pointerEvents = 'none';
                    backdrop.style.zIndex = '-1';
                }
            }""")

            # Fill email and password
            email_loc = page.locator('input[type=email]')
            email_loc.click()
            email_loc.fill(CREXI_EMAIL)
            time.sleep(0.3)

            pass_loc = page.locator('input[type=password]')
            pass_loc.click()
            pass_loc.fill(CREXI_PASSWORD)
            time.sleep(0.3)

            # Submit
            submit = page.query_selector('button[type=submit]')
            if submit:
                submit.click()
            else:
                page.keyboard.press("Enter")

            logger.info("[Crexi] Submitted login form — waiting for auth...")
            return True

        except Exception as e:
            logger.error(f"[Crexi] Login error: {e}")
            return False

    def _search_with_token(self, page, access_token: str) -> list[RawListing]:
        """Search Crexi via POST /assets/search using browser fetch (has Cloudflare cookies).

        API returns {data: [...], totalCount: N}
        Each item has: id, name, types, askingPrice, locations[{city, state, zip}], etc.
        Location filter: stateCode='TX' works; text location doesn't filter properly.
        We filter DFW cities post-hoc from results.
        """
        all_listings = []
        seen_ids = set()

        DFW_CITIES = {
            "dallas", "fort worth", "arlington", "garland", "irving", "frisco",
            "plano", "mesquite", "mckinney", "grand prairie", "carrollton",
            "denton", "lewisville", "richardson", "allen", "flower mound",
            "the colony", "mansfield", "grapevine", "euless", "hurst",
            "bedford", "addison", "rowlett", "rockwall", "wylie", "murphy",
            "little elm", "keller", "haltom city", "north richland hills",
        }

        search_body = {
            "transactionType": "Sale",
            "types": ["Multifamily"],
            "stateCode": "TX",
            "minUnits": SEARCH_CRITERIA["min_units"],
            "maxPrice": int(SEARCH_CRITERIA["max_price"]),
            "minPrice": int(SEARCH_CRITERIA["min_price"]),
            "includeUnpriced": False,
            "pageSize": 100,
            "page": 1,
        }
        body_json = json.dumps(search_body)

        logger.info("[Crexi] Calling POST /assets/search via browser fetch (TX stateCode filter)...")
        result = page.evaluate(f"""async () => {{
            const token = '{access_token}';
            try {{
                const resp = await fetch('https://api.crexi.com/assets/search', {{
                    method: 'POST',
                    headers: {{
                        'Authorization': 'Bearer ' + token,
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({body_json})
                }});
                if (!resp.ok) {{
                    const text = await resp.text();
                    return {{ status: resp.status, error: text }};
                }}
                const data = await resp.json();
                return {{ status: resp.status, data: data }};
            }} catch(e) {{
                return {{ error: e.message }};
            }}
        }}""")

        if result.get("error"):
            logger.warning(f"[Crexi] Fetch error: {result['error']}")
            return all_listings

        status = result.get("status")
        if status != 200:
            logger.warning(f"[Crexi] API {status}: {result.get('error', '')[:300]}")
            return all_listings

        data = result.get("data", {})
        items = data.get("data", data.get("results", data.get("assets", [])))
        total = data.get("totalCount", len(items))
        logger.info(f"[Crexi] API returned {len(items)} TX multifamily listings (total in TX: {total})")

        dfw_count = 0
        for raw in items:
            # Filter to DFW cities
            locs = raw.get("locations", [{}])
            city = (locs[0].get("city", "") if locs else "").lower()
            if city not in DFW_CITIES:
                continue
            dfw_count += 1
            listing = self._parse(raw)
            if listing and listing.external_id not in seen_ids:
                seen_ids.add(listing.external_id)
                all_listings.append(listing)

        logger.info(f"[Crexi] {dfw_count} DFW listings found, {len(all_listings)} qualify after filters")
        return all_listings

    def _parse(self, item: dict) -> Optional[RawListing]:
        try:
            price = self._get_price(item)
            units = self._get_units(item)
            if not price or not units:
                return None
            if price > SEARCH_CRITERIA["max_price"] or price < SEARCH_CRITERIA["min_price"]:
                return None
            if units < SEARCH_CRITERIA["min_units"]:
                return None

            # Crexi API returns address in locations[] array
            locs = item.get("locations", [])
            loc = locs[0] if locs else {}
            state_obj = loc.get("state", {})

            addr = item.get("address", {})
            if isinstance(addr, dict):
                street = addr.get("street", addr.get("line1", loc.get("address", "")))
                city   = addr.get("city", loc.get("city", ""))
                state  = addr.get("state", state_obj.get("code", "TX") if isinstance(state_obj, dict) else str(state_obj))
                zip_c  = str(addr.get("zip", addr.get("postalCode", loc.get("zip", ""))))
            else:
                street = loc.get("address", str(addr) if addr else "")
                city   = loc.get("city", item.get("city", ""))
                state  = state_obj.get("code", "TX") if isinstance(state_obj, dict) else "TX"
                zip_c  = loc.get("zip", item.get("zip", ""))

            prop_id = str(item.get("id", item.get("assetId", item.get("listingId", ""))))
            slug = item.get("slug", item.get("urlSlug", prop_id))

            return RawListing(
                source="crexi",
                external_id=prop_id,
                url=f"https://www.crexi.com/properties/{slug}",
                address=street,
                city=city,
                state=state,
                zip_code=zip_c,
                price=float(price),
                units=int(units),
                year_built=self._si(item.get("yearBuilt")),
                gross_monthly_rent=self._sf(item.get("grossMonthlyRent") or item.get("monthlyGrossRent")),
                annual_noi=self._sf(item.get("noi") or item.get("annualNOI")),
                cap_rate_listed=self._sf(item.get("capRate") or item.get("capRateOffered")),
                price_per_unit=float(price) / int(units),
                sqft=self._si(item.get("buildingSize") or item.get("totalSqFt")),
                lot_sqft=None,
                description=item.get("description", ""),
                listing_date=item.get("listedDate", item.get("listingDate", "")),
                days_on_market=self._si(item.get("daysOnMarket")),
                property_class=self._cls(item),
                occupancy_rate=self._sf(item.get("occupancyRate") or item.get("occupancy")),
                raw_data=item,
            )
        except Exception as e:
            logger.debug(f"[Crexi] Parse error: {e}")
            return None

    def _get_price(self, item):
        for k in ["askingPrice", "listingPrice", "price", "salePrice"]:
            v = item.get(k)
            if v:
                try:
                    return float(str(v).replace("$","").replace(",",""))
                except ValueError:
                    pass
        return None

    def _get_units(self, item):
        for k in ["numberOfUnits", "units", "unitCount", "totalUnits", "numUnits"]:
            v = item.get(k)
            if v:
                try:
                    return int(v)
                except ValueError:
                    pass
        return None

    def _cls(self, item):
        year = self._si(item.get("yearBuilt"))
        desc = (item.get("description","") + item.get("name","")).lower()
        for c in ["class a","class b","class c"]:
            if c in desc:
                return c[-1].upper()
        if year:
            return "A" if year >= 2000 else ("B" if year >= 1985 else "C")
        return None

    def _si(self, v):
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    def _sf(self, v):
        try:
            if v is None:
                return None
            f = float(str(v).replace("$","").replace(",","").replace("%","").strip())
            return f / 100 if "%" in str(v) and f > 1 else f
        except (ValueError, TypeError):
            return None
