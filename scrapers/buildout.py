"""Buildout.com JSON API scraper for commercial real estate listings."""

import logging
from typing import Optional

from scrapers.base import BaseScraper, RawListing
from config import BUILDOUT_TOKENS, SEARCH_CRITERIA

logger = logging.getLogger(__name__)

# Extract DFW city names (strip state) for matching
DFW_CITIES = [m.split(",")[0].strip().lower() for m in SEARCH_CRITERIA["markets"]]

MULTIFAMILY_KEYWORDS = ("multifamily", "multi-family", "apartment")


class BuildoutScraper(BaseScraper):
    """Polls the Buildout JSON inventory API for each configured brokerage."""

    def scrape(self, markets: Optional[list] = None) -> list:
        all_listings: list[RawListing] = []

        for brokerage, creds in BUILDOUT_TOKENS.items():
            token = creds["token"]
            domain = creds["domain"]
            url = (
                f"https://buildout.com/plugins/{token}/{domain}"
                f"/inventory/?format=json"
            )
            logger.info(f"[Buildout] Fetching {brokerage} inventory: {url}")

            try:
                resp = self.get(url)
                if resp is None:
                    logger.warning(f"[Buildout] No response from {brokerage}")
                    continue

                data = resp.json()
                # Buildout wraps listings under "inventory" key
                if isinstance(data, dict):
                    listings = data.get("inventory", data.get("listings", data.get("results", [])))
                else:
                    listings = data

                for item in listings:
                    parsed = self._parse_listing(item, brokerage)
                    if parsed is not None:
                        all_listings.append(parsed)

            except Exception as e:
                logger.error(f"[Buildout] Error fetching {brokerage}: {e}")
                continue

        logger.info(f"[Buildout] Total qualified listings: {len(all_listings)}")
        return all_listings

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _parse_listing(self, item: dict, brokerage: str) -> Optional[RawListing]:
        """Convert a single Buildout JSON object to a RawListing, or None if filtered out."""

        # --- Status filters ---
        if not item.get("sale", False):
            return None
        if item.get("under_contract", False):
            return None
        if item.get("closed", False):
            return None

        # --- Geography filters ---
        state = (item.get("state") or "").strip()
        if state.upper() != "TX":
            return None

        city = (item.get("city") or "").strip()
        # Accept any TX city — DFW metro is wide and we don't want to miss deals
        # The financial analyzer will filter further if needed

        # --- Property-type filter ---
        # index_attributes is a list of [key, value] pairs, not a dict
        raw_index = item.get("index_attributes") or []
        index_attrs = {}
        for pair in raw_index:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                index_attrs[pair[0].lower().replace(" ", "_")] = pair[1]

        prop_type = str(index_attrs.get("property_type", "")).lower()
        if not any(kw in prop_type for kw in MULTIFAMILY_KEYWORDS):
            return None

        # --- Map fields (gracefully handle missing data) ---
        address = item.get("address_one_line") or item.get("display_name") or ""
        zip_code = str(item.get("zip") or "")
        show_link = item.get("show_link") or ""
        pdf_url = item.get("pdf_url") or ""
        units = self._safe_int(index_attrs.get("number_of_units"))
        display_name = item.get("display_name") or ""

        # Broker contact info
        broker_contacts = item.get("broker_contacts") or []
        broker_info = "; ".join(
            f"{c.get('name', '')} ({c.get('email', '')}, {c.get('phone', '')})"
            for c in broker_contacts
            if c.get("name")
        )

        deal_status = item.get("deal_status_label_override") or "ON MARKET"

        # Build a useful description
        description_parts = [display_name]
        if broker_info:
            description_parts.append(f"Brokers: {broker_info}")
        if deal_status:
            description_parts.append(f"Status: {deal_status}")
        if pdf_url:
            description_parts.append(f"OM: {pdf_url}")
        description = " | ".join(p for p in description_parts if p)

        # Construct a stable external ID
        external_id = f"buildout-{brokerage}-{zip_code}-{display_name}".replace(" ", "-").lower()

        return RawListing(
            source=f"buildout:{brokerage}",
            external_id=external_id,
            url=show_link,
            address=address,
            city=city,
            state=state,
            zip_code=zip_code,
            price=0.0,              # Buildout inventory JSON rarely includes price
            units=units or 0,
            year_built=None,
            gross_monthly_rent=None,
            annual_noi=None,
            cap_rate_listed=None,
            price_per_unit=None,
            sqft=None,
            lot_sqft=None,
            description=description,
            listing_date=None,
            days_on_market=None,
            property_class=None,
            occupancy_rate=None,
            raw_data=item,
        )

    @staticmethod
    def _safe_int(value) -> Optional[int]:
        """Try to coerce a value to int; return None on failure."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
