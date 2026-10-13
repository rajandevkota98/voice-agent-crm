import json
from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from voice_crm import crm, sheets, voice
from voice_crm.config import Settings
from voice_crm.exceptions import SheetError
from voice_crm.server import create_app

SHEET_URL = "https://script.google.com/macros/s/TEST/exec"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _live_sheet(**overrides):
    return replace(Settings(dry_run=False, sheet_webhook_url=SHEET_URL), **overrides)


def test_append_row_posts_json_to_the_sheet():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    row = {"name": "Asha", "phone": "+9779800000000", "status": "interested", "transcript": "Caller: yes"}

    assert sheets.append_row(_live_sheet(), row, client=_client(handler)) is True
    assert seen["body"] == row


def test_append_row_refusal_raises():
    client = _client(lambda request: httpx.Response(200, json={"ok": False, "error": "no sheet"}))

    with pytest.raises(SheetError):
        sheets.append_row(_live_sheet(), {"name": "x"}, client=client)


def test_append_row_skips_when_not_configured():
    assert sheets.append_row(Settings(dry_run=False), {"name": "x"}) is False


def test_append_row_dry_run_makes_no_call():
    def fail_if_called(request):
        raise AssertionError("dry run must not write to the sheet")

    assert sheets.append_row(replace(_live_sheet(), dry_run=True), {"name": "x"}, client=_client(fail_if_called)) is False


def test_place_call_asks_twilio_to_report_completion():
    seen = {}

    def handler(request):
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(201, json={"sid": "CA1"})

    live = Settings(
        dry_run=False,
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_from_number="+15550001111",
    )
    voice.place_call(
        live,
        "+9779800000000",
        "https://example.test/voice/greet?lead_id=1",
        status_callback=voice.status_url("https://example.test", 1),
        client=_client(handler),
    )

    assert seen["form"]["StatusCallback"] == ["https://example.test/voice/status?lead_id=1"]
    assert seen["form"]["StatusCallbackEvent"] == ["completed"]


@pytest.fixture
def sheet_settings(settings):
    return replace(settings, dry_run=False, sheet_webhook_url=SHEET_URL)


def test_completed_call_writes_a_row_with_the_transcript(sheet_settings):
    with crm.connect(sheet_settings.db_path) as conn:
        lead_id = crm.add_lead(conn, "Asha", "+9779800000000")
        crm.log_interaction(conn, lead_id, "call", "out", "Hi Asha, are you still interested?")
        crm.log_interaction(conn, lead_id, "call", "in", "yes, tell me more")
        crm.set_status(conn, lead_id, "interested")

    posted = []

    def handler(request):
        if request.url.host == "script.google.com":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    client = TestClient(create_app(sheet_settings, http_client=_client(handler)))
    response = client.post("/voice/status", params={"lead_id": lead_id}, data={"CallStatus": "completed"})

    assert response.status_code == 204
    assert len(posted) == 1
    assert posted[0]["name"] == "Asha"
    assert posted[0]["status"] == "interested"
    assert "Caller: yes, tell me more" in posted[0]["transcript"]


def test_inbound_call_is_matched_by_caller_number(sheet_settings):
    with crm.connect(sheet_settings.db_path) as conn:
        crm.add_lead(conn, "Bikash", "+9779811111111")

    posted = []

    def handler(request):
        if request.url.host == "script.google.com":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    client = TestClient(create_app(sheet_settings, http_client=_client(handler)))
    response = client.post("/voice/status", data={"CallStatus": "completed", "From": "+9779811111111"})

    assert response.status_code == 204
    assert posted and posted[0]["name"] == "Bikash"


def test_status_other_than_completed_is_ignored(sheet_settings):
    client = TestClient(create_app(sheet_settings, http_client=_client(lambda r: httpx.Response(404))))

    response = client.post("/voice/status", params={"lead_id": 1}, data={"CallStatus": "ringing"})

    assert response.status_code == 204
