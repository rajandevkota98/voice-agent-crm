"""Decides who needs a follow-up and on which channel, then carries it out.

The rules, in plain words:
- A lead we have never written to gets a WhatsApp message.
- A lead who said they are interested gets one call.
- A lead who ignored WhatsApp for two days gets a call, unless they replied since.
- Leads who said no, or were won or lost, are never contacted again.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from voice_crm import crm, voice, whatsapp
from voice_crm.config import require
from voice_crm.exceptions import CRMError, VoiceCRMError
from voice_crm.logger import get_logger

log = get_logger("followups")

CALL_AFTER_SILENCE = timedelta(days=2)
STOP_STATUSES = ("not_interested", "won", "lost")


@dataclass(frozen=True)
class FollowupPlan:
    channel: str
    reason: str


def _parse(stamp):
    return datetime.fromisoformat(stamp)


def _last_time(interactions, channel=None, direction=None):
    times = [
        _parse(item["created_at"])
        for item in interactions
        if (channel is None or item["channel"] == channel)
        and (direction is None or item["direction"] == direction)
    ]
    return max(times, default=None)


def plan_followup(lead, interactions, now):
    if lead["status"] in STOP_STATUSES:
        return None

    last_call = _last_time(interactions, channel="call", direction="out")
    if lead["status"] == "interested":
        return None if last_call else FollowupPlan("call", "lead said they are interested")

    last_out = _last_time(interactions, direction="out")
    if last_out is None:
        return FollowupPlan("whatsapp", "first contact")

    last_whatsapp = _last_time(interactions, channel="whatsapp", direction="out")
    if last_whatsapp is None:
        return None
    if last_call and last_call > last_whatsapp:
        return None
    last_reply = _last_time(interactions, direction="in")
    if last_reply and last_reply > last_whatsapp:
        return None
    if now - last_whatsapp < CALL_AFTER_SILENCE:
        return None
    return FollowupPlan("call", "no reply to WhatsApp")


def _execute(conn, settings, followup):
    lead = crm.get_lead(conn, followup["lead_id"])
    if lead is None:
        raise CRMError(f"Follow-up {followup['id']} points at missing lead {followup['lead_id']}")

    if followup["channel"] == "whatsapp":
        # First contact is outside the 24-hour window, so it has to be an approved template.
        template = settings.whatsapp_template_name
        whatsapp.send_template(settings, lead["phone"], template, settings.whatsapp_template_language)
        crm.log_interaction(conn, lead["id"], "whatsapp", "out", f"[template {template}]")
    elif followup["channel"] == "call":
        base = require(settings.public_base_url, "PUBLIC_BASE_URL")
        call_sid = voice.place_call(
            settings,
            lead["phone"],
            voice.greet_url(base, lead["id"]),
            status_callback=voice.status_url(base, lead["id"]),
        )
        crm.log_interaction(conn, lead["id"], "call", "out", f"Call placed ({call_sid})")
    else:
        raise CRMError(f"Unknown channel {followup['channel']!r}")

    if lead["status"] == "new":
        crm.set_status(conn, lead["id"], "contacted")


def run_followups(conn, settings, now=None):
    now = now or datetime.now(timezone.utc)
    stats = {"planned": 0, "sent": 0, "failed": 0}

    for lead in crm.list_leads(conn):
        plan = plan_followup(lead, crm.list_interactions(conn, lead["id"]), now)
        if plan is None or crm.has_pending_followup(conn, lead["id"], plan.channel):
            continue
        crm.schedule_followup(conn, lead["id"], plan.channel, plan.reason, due_at=now.isoformat(timespec="microseconds"))
        stats["planned"] += 1

    for followup in crm.pending_followups(conn, now.isoformat(timespec="microseconds")):
        try:
            _execute(conn, settings, followup)
            crm.complete_followup(conn, followup["id"])
            stats["sent"] += 1
        except VoiceCRMError as exc:
            log.error("Follow-up id=%s failed: %s", followup["id"], exc)
            stats["failed"] += 1

    log.info("Follow-up run finished: %s", stats)
    return stats
