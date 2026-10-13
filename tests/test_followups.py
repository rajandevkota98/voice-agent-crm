from dataclasses import replace
from datetime import datetime, timedelta, timezone

from voice_crm import crm
from voice_crm.followups import plan_followup, run_followups

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _lead(status="contacted"):
    return {"id": 1, "name": "Asha", "phone": "+9779800000000", "status": status}


def _event(channel, direction, when):
    return {"channel": channel, "direction": direction, "created_at": when.isoformat()}


def test_untouched_lead_gets_a_whatsapp_first():
    plan = plan_followup(_lead("new"), [], NOW)

    assert plan.channel == "whatsapp"


def test_lead_who_said_no_is_never_planned():
    plan = plan_followup(_lead("not_interested"), [_event("whatsapp", "out", NOW - timedelta(days=9))], NOW)

    assert plan is None


def test_silent_lead_gets_a_call_after_two_days():
    history = [_event("whatsapp", "out", NOW - timedelta(days=3))]

    plan = plan_followup(_lead(), history, NOW)

    assert plan.channel == "call"


def test_no_call_yet_when_whatsapp_was_only_a_day_ago():
    history = [_event("whatsapp", "out", NOW - timedelta(days=1))]

    assert plan_followup(_lead(), history, NOW) is None


def test_a_reply_stops_the_call_escalation():
    history = [
        _event("whatsapp", "out", NOW - timedelta(days=3)),
        _event("whatsapp", "in", NOW - timedelta(days=2)),
    ]

    assert plan_followup(_lead(), history, NOW) is None


def test_only_one_call_is_planned_for_silent_lead():
    history = [
        _event("whatsapp", "out", NOW - timedelta(days=3)),
        _event("call", "out", NOW - timedelta(days=1)),
    ]

    assert plan_followup(_lead(), history, NOW) is None


def test_interested_lead_gets_one_call():
    plan = plan_followup(_lead("interested"), [_event("whatsapp", "in", NOW)], NOW)

    assert plan.channel == "call"


def test_run_followups_sends_whatsapp_then_stops_until_due(settings, conn):
    crm.add_lead(conn, "Asha", "+9779800000000")

    first = run_followups(conn, settings, now=NOW)
    second = run_followups(conn, settings, now=NOW)

    assert first == {"planned": 1, "sent": 1, "failed": 0}
    assert second == {"planned": 0, "sent": 0, "failed": 0}
    lead = crm.find_lead_by_phone(conn, "+9779800000000")
    assert lead["status"] == "contacted"
    outbound = [i for i in crm.list_interactions(conn, lead["id"]) if i["direction"] == "out"]
    assert len(outbound) == 1
    assert outbound[0]["channel"] == "whatsapp"


def test_run_followups_counts_failures_and_keeps_going(settings, conn):
    broken = replace(settings, dry_run=False)
    crm.add_lead(conn, "Asha", "+9779800000000")

    stats = run_followups(conn, broken, now=NOW)

    assert stats["failed"] == 1
    assert stats["sent"] == 0
