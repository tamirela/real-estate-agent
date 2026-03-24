"""Base scraper class with shared HTTP logic."""

import time
import logging
import requests
import cloudscraper
from dataclasses import dataclass, field
from typing import Optional
from config import SCRAPER

logger = logging.getLogger(__name__)


@dataclass
class RawListing:
    """Normalized listing data from any source."""
    source: str
    external_id: str
    url: str
    address: str
    city: str
    state: str
    zip_code: str
    price: float
    units: int
    year_built: Optional[int]
    gross_monthly_rent: Optional[float]
    annual_noi: Optional[float]
    cap_rate_listed: Optional[float]
    price_per_unit: Optional[float]
    sqft: Optional[int]
    lot_sqft: Optional[int]
    description: str
    listing_date: Optional[str]
    days_on_market: Optional[int]
    property_class: Optional[str]
    occupancy_rate: Optional[float]
    raw_data: dict = field(default_factory=dict)


class BaseScraper:
    def __init__(self):
        # cloudscraper bypasses Cloudflare bot protection (used by Crexi, LoopNet, etc.)
        self.session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "darwin", "desktop": True}
        )
        self.session.headers.update({
            "User-Agent": SCRAPER["user_agent"],
            "Accept": "application/json, text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        self.delay = SCRAPER["request_delay_seconds"]
        self.timeout = SCRAPER["timeout_seconds"]
        self.max_retries = SCRAPER["max_retries"]

    def get(self, url: str, **kwargs) -> Optional[requests.Response]:
        """GET with retry logic and polite delays."""
        for attempt in range(self.max_retries):
            try:
                time.sleep(self.delay)
                response = self.session.get(url, timeout=self.timeout, **kwargs)
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"Rate limited. Waiting {wait}s...")
                    time.sleep(wait)
                elif e.response.status_code in (403, 401):
                    logger.error(f"Access denied for {url}")
                    return None
                else:
                    logger.warning(f"HTTP {e.response.status_code} for {url} (attempt {attempt+1})")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request failed for {url}: {e} (attempt {attempt+1})")
                time.sleep(5 * (attempt + 1))
        return None

    def scrape(self, markets: list[str]) -> list[RawListing]:
        raise NotImplementedError
