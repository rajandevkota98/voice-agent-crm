import pytest

from voice_crm import crm
from voice_crm.exceptions import CRMError


def test_normalize_phone_accepts_spaces_and_dashes():
    assert crm.normalize_phone("+977 980-000 0000") == "+9779800000000"


def test_normalize_phone_rejects_numbers_without_country_code():
    with pytest.raises(CRMError):
        crm.normalize_phone("9800000000")


def test_add_and_find_lead(conn):
    lead_id = crm.add_lead(conn, "  Asha Gurung ", "+9779800000000")

    lead = crm.find_lead_by_phone(conn, "+977 980 000 0000")

    assert lead["id"] == lead_id
    assert lead["name"] == "Asha Gurung"
    assert lead["status"] == "new"


def test_duplicate_phone_is_rejected(conn):
    crm.add_lead(conn, "Asha", "+9779800000000")

    with pytest.raises(CRMError, match="already exists"):
        crm.add_lead(conn, "Someone else", "+9779800000000")


def test_set_status_rejects_unknown_status(conn):
    lead_id = crm.add_lead(conn, "Asha", "+9779800000000")

    with pytest.raises(CRMError):
        crm.set_status(conn, lead_id, "maybe")


def test_interactions_are_kept_in_order(conn):
    lead_id = crm.add_lead(conn, "Asha", "+9779800000000")
    crm.log_interaction(conn, lead_id, "whatsapp", "out", "hello")
    crm.log_interaction(conn, lead_id, "whatsapp", "in", "hi there")

    bodies = [item["body"] for item in crm.list_interactions(conn, lead_id)]

    assert bodies == ["hello", "hi there"]


def test_pending_followups_respect_due_time(conn):
    lead_id = crm.add_lead(conn, "Asha", "+9779800000000")
    crm.schedule_followup(conn, lead_id, "call", "later", due_at="2030-01-01T00:00:00+00:00")

    assert crm.pending_followups(conn, "2029-12-31T00:00:00+00:00") == []
    assert len(crm.pending_followups(conn, "2030-01-02T00:00:00+00:00")) == 1
