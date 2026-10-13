"""What happens once a call is over: the HubSpot sync and one row in the sheet.

Each step is isolated, so a HubSpot outage does not stop the sheet row, and the
other way round. Nothing here raises to the caller.
"""

from voice_crm import crm, sheets, sync
from voice_crm.exceptions import VoiceCRMError
from voice_crm.logger import get_logger

log = get_logger("wrapup")

TRANSCRIPT_LINES = 20


def _transcript(interactions):
    calls = [i for i in interactions if i["channel"] == "call"][-TRANSCRIPT_LINES:]
    who = {"in": "Caller", "out": "Agent"}
    return "\n".join(f"{who[i['direction']]}: {i['body']}" for i in calls)


def finish_call(conn, settings, lead_id, client=None):
    lead = crm.get_lead(conn, lead_id)
    if lead is None:
        log.error("Call finished for missing lead id=%s, nothing to log", lead_id)
        return

    try:
        sync.sync_pending(conn, settings, client=client)
    except VoiceCRMError as exc:
        log.error("HubSpot sync after the call failed: %s", exc)

    row = {
        "name": lead["name"],
        "phone": lead["phone"],
        "status": lead["status"],
        "transcript": _transcript(crm.list_interactions(conn, lead_id)),
    }
    try:
        sheets.append_row(settings, row, client=client)
    except VoiceCRMError as exc:
        log.error("Google Sheet row after the call failed: %s", exc)
    log.info("Finished call wrap-up for lead id=%s", lead_id)
