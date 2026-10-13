"""Appends one row per finished call to a Google Sheet.

The sheet is reached through a small Google Apps Script web app that you deploy
from the sheet itself (no Google Cloud project, no billing). See README.
"""

import httpx

from voice_crm.exceptions import SheetError
from voice_crm.logger import get_logger
from voice_crm.transport import TIMEOUT_SECONDS, post_json

log = get_logger("sheets")


def append_row(settings, row, client=None):
    if not settings.sheet_webhook_url:
        log.info("Google Sheet is not configured (GOOGLE_SHEET_WEBHOOK_URL is empty); skipping")
        return False
    if settings.dry_run:
        log.info("[dry-run] would append to the sheet: %s", row)
        return False

    # Apps Script answers POST with a redirect, so this client has to follow it.
    owns_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
    try:
        response = post_json(settings.sheet_webhook_url, SheetError, client=http, json=row)
    finally:
        if owns_client:
            http.close()

    if not response.get("ok"):
        raise SheetError(f"Sheet webhook refused the row: {response}")
    log.info("Appended call to the Google Sheet for %s", row.get("name"))
    return True
