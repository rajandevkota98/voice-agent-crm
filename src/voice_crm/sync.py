"""Copies leads and conversation notes from SQLite out to HubSpot.

SQLite stays the agent's working store, so the agent keeps running if HubSpot is
down. This module only writes two things back locally: the HubSpot contact id on
each lead, and a synced flag on each interaction. Re-running is safe.
"""

from voice_crm import crm, hubspot
from voice_crm.exceptions import HubSpotError, VoiceCRMError
from voice_crm.logger import get_logger

log = get_logger("sync")


def sync_pending(conn, settings, client=None):
    stats = {"contacts": 0, "notes": 0, "failed": 0}
    if not settings.hubspot_token:
        log.info("HubSpot is not configured (HUBSPOT_ACCESS_TOKEN is empty); skipping sync")
        return stats

    leads = crm.list_leads(conn)
    notes = crm.unsynced_interactions(conn)
    if settings.dry_run:
        log.info("[dry-run] would sync %s leads and %s notes to HubSpot", len(leads), len(notes))
        return stats

    for lead in leads:
        try:
            _sync_lead(conn, settings.hubspot_token, lead, client)
            stats["contacts"] += 1
        except VoiceCRMError as exc:
            log.error("Could not sync lead id=%s to HubSpot: %s", lead["id"], exc)
            stats["failed"] += 1

    for item in notes:
        try:
            _sync_note(conn, settings.hubspot_token, item, client)
            stats["notes"] += 1
        except VoiceCRMError as exc:
            log.error("Could not sync interaction id=%s to HubSpot: %s", item["id"], exc)
            stats["failed"] += 1

    log.info("HubSpot sync finished: %s", stats)
    return stats


def _sync_lead(conn, token, lead, client):
    contact_id = lead["hubspot_id"] or hubspot.find_contact_id(token, lead["phone"], client)
    if contact_id:
        hubspot.update_contact(token, contact_id, lead["name"], lead["phone"], lead["status"], client)
    else:
        contact_id = hubspot.create_contact(token, lead["name"], lead["phone"], lead["status"], client)

    if contact_id != lead["hubspot_id"]:
        crm.set_hubspot_id(conn, lead["id"], contact_id)
    log.info("Synced lead id=%s to HubSpot contact %s", lead["id"], contact_id)


def _sync_note(conn, token, item, client):
    lead = crm.get_lead(conn, item["lead_id"])
    if not lead["hubspot_id"]:
        raise HubSpotError(f"lead id={lead['id']} has no HubSpot contact yet")

    body = f"[{item['channel']} {item['direction']}] {item['body']}"
    hubspot.create_note(token, lead["hubspot_id"], body, item["created_at"], client)
    crm.mark_interaction_synced(conn, item["id"])
