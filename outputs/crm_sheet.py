"""
CRM Google Sheet module.
Appends a new row to the deal-tracking CRM spreadsheet using
the Google Sheets API v4.

Uses the same OAuth refresh-token flow as drive_output.
No extra dependencies -- only urllib.request.
"""

import json
import logging
import os
import urllib.request
import urllib.parse
from datetime import date

from config import CRM_SHEET_ID, DRIVE_CONFIG

logger = logging.getLogger(__name__)

# ── Credential paths (from config) ──────────────────────────────
_CRED_PATH = DRIVE_CONFIG.get("credentials_path",
              os.path.expanduser("~/.mcp-gdrive/credentials.json"))
_KEYS_PATH = DRIVE_CONFIG.get("oauth_keys_path",
              os.path.expanduser("~/.mcp-gdrive/gcp-oauth.keys.json"))


def _get_access_token() -> str:
    """Exchange the stored refresh token for a fresh access token."""
    with open(_CRED_PATH, "r") as f:
        creds = json.load(f)
    with open(_KEYS_PATH, "r") as f:
        keys_data = json.load(f)

    client_info = keys_data.get("installed") or keys_data.get("web") or keys_data
    client_id = client_info["client_id"]
    client_secret = client_info["client_secret"]

    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh_token found in credentials.json")

    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    return data["access_token"]


def _fmt(value, fmt_type="str"):
    """Safely format a value for the spreadsheet row."""
    if value is None:
        return ""
    if fmt_type == "dollar":
        try:
            return f"${float(value):,.0f}"
        except (ValueError, TypeError):
            return str(value)
    if fmt_type == "pct":
        try:
            return f"{float(value):.1%}"
        except (ValueError, TypeError):
            return str(value)
    return str(value)


class CrmSheet:
    """
    Appends deal data as a new row in the CRM Google Sheet.
    """

    APPEND_RANGE = "Sheet1!A:U"  # columns A through U

    def __init__(self):
        if not CRM_SHEET_ID:
            raise RuntimeError(
                "CRM_SHEET_ID is empty. "
                "Set CRM_SHEET_ID env var or update config.py."
            )
        self.sheet_id = CRM_SHEET_ID

    def append_deal(self, deal_data: dict, drive_folder_link: str = "") -> dict:
        """
        Append a single deal row to the CRM sheet.

        Parameters
        ----------
        deal_data : dict
            Full deal dictionary (property info + financial metrics).
        drive_folder_link : str
            Web link to the Google Drive analysis folder.

        Returns
        -------
        dict  Google Sheets API response.
        """
        token = _get_access_token()

        row = [
            date.today().isoformat(),                                          # A  date
            _fmt(deal_data.get("source")),                                     # B  source
            _fmt(deal_data.get("property_name")),                              # C  property_name
            _fmt(deal_data.get("address")),                                    # D  address
            _fmt(deal_data.get("city")),                                       # E  city
            _fmt(deal_data.get("units")),                                      # F  units
            _fmt(deal_data.get("price"), "dollar"),                            # G  price
            _fmt(deal_data.get("price_per_unit"), "dollar"),                   # H  price_per_door
            _fmt(deal_data.get("current_rent_sf")),                            # I  current_rent_sf
            _fmt(deal_data.get("market_rent_sf")),                             # J  market_rent_sf
            _fmt(deal_data.get("spread_pct"), "pct"),                          # K  spread_pct
            _fmt(deal_data.get("noi"), "dollar"),                              # L  noi
            _fmt(deal_data.get("cap_rate"), "pct"),                            # M  cap_rate
            _fmt(deal_data.get("verdict")),                                    # N  verdict
            _fmt(deal_data.get("reason")),                                     # O  reason
            _fmt(deal_data.get("broker_name")),                                # P  broker_name
            _fmt(deal_data.get("broker_email")),                               # Q  broker_email
            _fmt(deal_data.get("broker_phone")),                               # R  broker_phone
            drive_folder_link,                                                 # S  drive_folder_link
            "New",                                                             # T  status
            _fmt(deal_data.get("notes")),                                      # U  notes
        ]

        body = json.dumps({
            "values": [row],
        }).encode()

        encoded_range = urllib.parse.quote(self.APPEND_RANGE)
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}"
            f"/values/{encoded_range}:append"
            f"?valueInputOption=USER_ENTERED"
            f"&insertDataOption=INSERT_ROWS"
        )

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        logger.info("Appending row to CRM sheet %s", self.sheet_id)
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())

        logger.info("CRM row appended: %s", result.get("updates", {}).get("updatedRange", ""))
        return result
