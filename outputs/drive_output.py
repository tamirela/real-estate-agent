"""
Google Drive output module.
Creates a property analysis folder and uploads Pro Forma, Sensitivity, and
Executive Summary files using the Google Drive API v3.

Uses OAuth refresh-token flow with credentials stored by the MCP gdrive helper.
No extra dependencies -- only urllib.request.
"""

import json
import logging
import os
import tempfile
import urllib.request
import urllib.parse

from config import DRIVE_CONFIG
from outputs.templates import pro_forma, sensitivity, exec_summary

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

    # gcp-oauth.keys.json may nest under "installed" or "web"
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


def _drive_create_folder(name: str, parent_id: str, token: str) -> dict:
    """Create a folder in Google Drive. Returns metadata dict with id + webViewLink."""
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    body = json.dumps(metadata).encode()
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/files?fields=id,webViewLink",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _multipart_upload(filepath: str, name: str, mime_type: str,
                      parent_id: str, token: str) -> dict:
    """Upload a file using multipart upload to Google Drive API v3."""
    boundary = "----MultipartBoundary7ma4d9abcdef"
    metadata = json.dumps({
        "name": name,
        "parents": [parent_id],
    })

    with open(filepath, "rb") as f:
        file_bytes = f.read()

    # Build the multipart body
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=multipart&fields=id,webViewLink",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


class DriveOutput:
    """
    Creates a Google Drive folder for a deal and uploads the three
    analysis documents (Pro Forma, Sensitivity, Executive Summary).
    """

    def __init__(self):
        self.parent_folder_id = DRIVE_CONFIG["property_analysis_folder_id"]
        if not self.parent_folder_id:
            raise RuntimeError(
                "DRIVE_CONFIG['property_analysis_folder_id'] is empty. "
                "Update config.py with the Drive folder ID."
            )

    def upload(self, deal_data: dict) -> dict:
        """
        Generate and upload all three analysis files for a deal.

        Parameters
        ----------
        deal_data : dict
            Full deal dictionary with property info, financials, flags, etc.

        Returns
        -------
        dict with keys: folder_id, folder_link
        """
        token = _get_access_token()
        property_name = deal_data.get("property_name", "Unknown Property")
        folder_name = f"{property_name} - Analysis"

        logger.info("Creating Drive folder: %s", folder_name)
        folder = _drive_create_folder(folder_name, self.parent_folder_id, token)
        folder_id = folder["id"]
        folder_link = folder.get("webViewLink", "")

        with tempfile.TemporaryDirectory() as tmp:
            # 1. Pro Forma xlsx
            pf_path = os.path.join(tmp, f"{property_name} - Pro Forma.xlsx")
            pro_forma.generate(deal_data, pf_path)
            logger.info("Uploading Pro Forma")
            _multipart_upload(
                pf_path,
                f"{property_name} - Pro Forma.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                folder_id, token,
            )

            # 2. Sensitivity Test xlsx
            st_path = os.path.join(tmp, f"{property_name} - Sensitivity Test.xlsx")
            sensitivity.generate(deal_data, st_path)
            logger.info("Uploading Sensitivity Test")
            _multipart_upload(
                st_path,
                f"{property_name} - Sensitivity Test.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                folder_id, token,
            )

            # 3. Executive Summary docx
            es_path = os.path.join(tmp, f"{property_name} - Executive Summary.docx")
            exec_summary.generate(deal_data, es_path)
            logger.info("Uploading Executive Summary")
            _multipart_upload(
                es_path,
                f"{property_name} - Executive Summary.docx",
                "application/vnd.openxmlformats-officedocument.document",
                folder_id, token,
            )

        logger.info("All files uploaded to folder %s", folder_link)
        return {
            "folder_id": folder_id,
            "folder_link": folder_link,
        }
