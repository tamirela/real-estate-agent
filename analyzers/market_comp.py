"""
Market comp analyzer — compares a property's rent/SF to market averages.

Uses RentCast API when available, falls back to hardcoded DFW zip-code averages.
"""

import logging
import requests
from typing import Optional
from scrapers.base import RawListing
from config import API_KEYS, SEARCH_CRITERIA

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# DFW fallback data: zip code → avg rent per SF (monthly)
# Class B/C multifamily estimates, updated for 2026
# ─────────────────────────────────────────────────────────────
DFW_RENT_PER_SF = {
    # Dallas core
    "75201": 2.10,   # Uptown
    "75202": 1.95,   # Downtown Dallas
    "75204": 1.80,   # Oak Lawn / Turtle Creek
    "75206": 1.65,   # Lower Greenville / M Streets
    "75207": 1.70,   # Design District
    "75208": 1.25,   # Oak Cliff North
    "75209": 1.55,   # Love Field area
    "75210": 1.05,   # South Dallas
    "75211": 1.10,   # West Dallas
    "75212": 1.05,   # West Dallas / La Bajada
    "75214": 1.50,   # Lakewood
    "75215": 1.00,   # South Dallas / Fair Park
    "75216": 0.95,   # South Oak Cliff
    "75217": 1.00,   # Pleasant Grove / Balch Springs
    "75218": 1.45,   # Casa Linda / White Rock
    "75219": 1.85,   # Oak Lawn
    "75220": 1.30,   # Northwest Dallas
    "75223": 1.20,   # East Dallas
    "75224": 1.10,   # Kessler Park / Stevens Park
    "75225": 1.75,   # University Park
    "75226": 1.35,   # Deep Ellum / East Dallas
    "75227": 1.05,   # Buckner Terrace
    "75228": 1.10,   # East Dallas / Lake Highlands South
    "75229": 1.35,   # North Dallas
    "75230": 1.60,   # Preston Hollow / North Dallas
    "75231": 1.40,   # Lake Highlands
    "75232": 1.00,   # Cedar Crest / South Oak Cliff
    "75233": 1.05,   # West Oak Cliff
    "75234": 1.30,   # Farmers Branch area
    "75235": 1.25,   # Love Field / Bachman Lake
    "75236": 1.00,   # Mountain Creek
    "75237": 0.95,   # DeSoto area
    "75238": 1.35,   # Lake Highlands
    "75240": 1.40,   # Far North Dallas
    "75241": 0.95,   # South Dallas / Lancaster
    "75243": 1.35,   # Lake Highlands / Richardson border
    "75244": 1.45,   # Addison area
    "75246": 1.50,   # Bryan Place / Downtown East
    "75247": 1.20,   # Stemmons Corridor
    "75248": 1.50,   # Far North Dallas
    "75249": 1.05,   # Cedar Hill area
    "75251": 1.55,   # Preston / Valley View
    "75252": 1.55,   # Far North Dallas
    "75253": 0.95,   # Seagoville

    # Fort Worth
    "76101": 1.30,   # Downtown Fort Worth
    "76102": 1.35,   # Downtown Fort Worth
    "76103": 1.10,   # Polytechnic / East FW
    "76104": 1.15,   # South Fort Worth / Fairmount
    "76105": 1.00,   # Stop Six / East FW
    "76106": 1.05,   # North Fort Worth / Stockyards
    "76107": 1.45,   # Cultural District / West 7th
    "76109": 1.40,   # TCU area
    "76110": 1.20,   # Near South Side
    "76111": 1.10,   # North Fort Worth
    "76112": 1.05,   # Handley / East FW
    "76115": 0.95,   # South Fort Worth
    "76116": 1.20,   # Ridglea / West FW
    "76119": 0.95,   # East Fort Worth
    "76120": 1.05,   # East Fort Worth
    "76123": 1.10,   # Southwest Fort Worth
    "76131": 1.25,   # Alliance / North FW
    "76132": 1.30,   # Wedgwood / South FW
    "76133": 1.20,   # Southwest Fort Worth
    "76134": 1.05,   # South Fort Worth
    "76137": 1.30,   # North Richland Hills / Watauga
    "76140": 1.00,   # Forest Hill

    # Arlington / Grand Prairie / Irving
    "76001": 1.15,   # South Arlington
    "76002": 1.20,   # Southeast Arlington
    "76006": 1.35,   # North Arlington
    "76010": 1.15,   # Central Arlington
    "76011": 1.25,   # North Arlington / AT&T area
    "76012": 1.10,   # Central Arlington
    "76013": 1.10,   # West Arlington
    "76014": 1.15,   # South Arlington
    "76015": 1.15,   # East Arlington
    "76016": 1.10,   # West Arlington
    "76017": 1.10,   # South Arlington
    "76018": 1.05,   # Southeast Arlington
    "75050": 1.15,   # Grand Prairie
    "75051": 1.10,   # Grand Prairie
    "75052": 1.20,   # Grand Prairie South
    "75060": 1.15,   # Irving East
    "75061": 1.20,   # Irving Central
    "75062": 1.30,   # Irving / Las Colinas
    "75063": 1.40,   # Las Colinas

    # Garland / Mesquite / Richardson
    "75040": 1.15,   # Garland Central
    "75041": 1.10,   # Garland South
    "75042": 1.15,   # Garland
    "75043": 1.10,   # Garland East
    "75044": 1.25,   # Garland / Firewheel
    "75080": 1.35,   # Richardson
    "75081": 1.30,   # Richardson
    "75082": 1.40,   # Richardson / Spring Creek
    "75149": 1.05,   # Mesquite
    "75150": 1.10,   # Mesquite

    # Plano / Frisco / McKinney
    "75023": 1.45,   # Plano East
    "75024": 1.55,   # Plano West
    "75025": 1.40,   # Plano North
    "75034": 1.55,   # Frisco
    "75035": 1.50,   # Frisco
    "75069": 1.35,   # McKinney
    "75070": 1.40,   # McKinney
    "75071": 1.35,   # McKinney

    # Carrollton / Denton / Lewisville
    "75006": 1.30,   # Carrollton
    "75007": 1.35,   # Carrollton
    "75010": 1.30,   # Carrollton
    "75019": 1.35,   # Coppell
    "75056": 1.50,   # The Colony
    "75057": 1.25,   # Lewisville
    "75067": 1.25,   # Lewisville
    "76201": 1.20,   # Denton
    "76205": 1.15,   # Denton
    "76207": 1.15,   # Denton
    "76209": 1.20,   # Denton
    "76210": 1.30,   # South Denton / Corinth

    # Other suburbs
    "75038": 1.35,   # Irving / Las Colinas
    "75039": 1.50,   # Irving / Las Colinas
    "75074": 1.30,   # Plano
    "75075": 1.35,   # Plano
    "75093": 1.50,   # Plano West
    "76039": 1.25,   # Euless
    "76040": 1.20,   # Euless
    "76053": 1.25,   # Hurst
    "76054": 1.30,   # Hurst / NRH
    "76118": 1.20,   # Richland Hills
    "76148": 1.15,   # Watauga / NRH
    "76180": 1.25,   # North Richland Hills
    "76182": 1.30,   # North Richland Hills
    "75104": 1.05,   # Cedar Hill
    "75115": 1.05,   # DeSoto
    "75116": 1.00,   # Duncanville
    "75134": 0.95,   # Lancaster
    "75146": 1.00,   # Lancaster
    "75154": 1.00,   # Glenn Heights
}

# Default DFW average for zip codes not in the lookup
DFW_DEFAULT_RENT_PER_SF = 1.33


class MarketCompAnalyzer:
    """Compares a property's rent per SF to market averages for its zip/area."""

    def analyze(self, listing: RawListing) -> dict:
        """
        Compare subject rent/SF to market rent/SF.

        Returns dict with:
            subject_rent_sf, market_rent_sf, spread_pct, spread_dollar,
            verdict, data_source
        """
        # ── Subject rent/SF ──────────────────────────────────────
        subject_rent_sf = self._calc_subject_rent_sf(listing)
        if subject_rent_sf is None:
            return self._empty_result("Insufficient data to calculate rent/SF")

        # ── Market rent/SF ───────────────────────────────────────
        market_rent_sf, data_source = self._get_market_rent_sf(listing)

        # ── Spread calculation ───────────────────────────────────
        if market_rent_sf <= 0:
            return self._empty_result("Could not determine market rent/SF")

        spread_dollar = market_rent_sf - subject_rent_sf
        spread_pct = spread_dollar / market_rent_sf if market_rent_sf > 0 else 0

        # ── Verdict ──────────────────────────────────────────────
        if spread_pct >= 0.15:
            verdict = "GO"
        elif spread_pct >= 0.05:
            verdict = "MAYBE"
        else:
            verdict = "NO-GO"

        return {
            "subject_rent_sf": round(subject_rent_sf, 2),
            "market_rent_sf": round(market_rent_sf, 2),
            "spread_pct": round(spread_pct, 4),
            "spread_dollar": round(spread_dollar, 2),
            "verdict": verdict,
            "data_source": data_source,
        }

    # ─────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────

    def _calc_subject_rent_sf(self, listing: RawListing) -> Optional[float]:
        """Calculate the subject property's rent per SF."""
        gross_monthly = listing.gross_monthly_rent
        if not gross_monthly or gross_monthly <= 0:
            return None

        sqft = listing.sqft
        if not sqft or sqft <= 0:
            # Estimate sqft from units: assume avg 750 SF/unit for Class B/C
            if listing.units and listing.units > 0:
                sqft = listing.units * 750
            else:
                return None

        return gross_monthly / sqft

    def _get_market_rent_sf(self, listing: RawListing) -> tuple[float, str]:
        """
        Get market rent/SF for the listing's area.
        Tries RentCast API first, falls back to DFW lookup table.
        """
        # ── Try RentCast API ─────────────────────────────────────
        rentcast_key = API_KEYS.get("rentcast", "")
        if rentcast_key:
            result = self._fetch_rentcast(listing.zip_code, rentcast_key)
            if result is not None:
                return result, "RentCast API"

        # ── Fallback: DFW zip code lookup ────────────────────────
        zip_code = listing.zip_code.strip() if listing.zip_code else ""
        if zip_code in DFW_RENT_PER_SF:
            return DFW_RENT_PER_SF[zip_code], f"DFW lookup (zip {zip_code})"

        return DFW_DEFAULT_RENT_PER_SF, "DFW default average ($1.33/SF)"

    def _fetch_rentcast(self, zip_code: str, api_key: str) -> Optional[float]:
        """
        Query RentCast API for market rent data.
        Returns rent per SF (monthly) or None on failure.
        """
        url = "https://api.rentcast.io/v1/avm/rent/long-term"
        params = {
            "zipCode": zip_code,
            "propertyType": "Apartment",
            "bedrooms": 2,
        }
        headers = {
            "Accept": "application/json",
            "X-Api-Key": api_key,
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()

            # RentCast returns rent estimate and sqft — compute rent/SF
            rent = data.get("rent") or data.get("rentRangeLow")
            sqft = data.get("sqft") or data.get("averageSqft")

            if rent and sqft and sqft > 0:
                return rent / sqft

            # Some responses provide rentPerSqft directly
            rent_per_sf = data.get("rentPerSqft")
            if rent_per_sf and rent_per_sf > 0:
                return rent_per_sf

            logger.warning(f"RentCast returned incomplete data for zip {zip_code}: {data}")
            return None

        except requests.exceptions.RequestException as e:
            logger.warning(f"RentCast API failed for zip {zip_code}: {e}")
            return None

    def _empty_result(self, reason: str) -> dict:
        """Return a result dict when analysis cannot be performed."""
        return {
            "subject_rent_sf": None,
            "market_rent_sf": None,
            "spread_pct": None,
            "spread_dollar": None,
            "verdict": None,
            "data_source": reason,
        }
