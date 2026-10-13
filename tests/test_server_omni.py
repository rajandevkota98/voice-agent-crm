import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

import html
import re
import time
from urllib.parse import urlsplit


def _finish_turn(client, response):
    """Twilio hands a reply off to /voice/wait and asks again until it's ready. Do the same here."""
    for _ in range(200):
        match = re.search(r'<Redirect method="POST">([^<]+)</Redirect>', response.text)
        if not match:
            return response
        parts = urlsplit(html.unescape(match.group(1)))
        time.sleep(0.02)
        response = client.post(f"{parts.path}?{parts.query}")
    raise AssertionError("the model reply never finished")


from voice_crm import crm
from voice_crm.server import create_app

RECORDING = "https://api.twilio.com/2010-04-01/Accounts/AC123/Recordings/RE1"


class FakeProviders:
    """Stands in for Twilio (recording download) and OpenRouter (model call)."""

    def __init__(self, model_content=None, model_status=200):
        self.model_content = model_content
        self.model_status = model_status
        self.model_calls = 0

    def __call__(self, request):
        if "/Recordings/" in request.url.path:
            return httpx.Response(200, content=b"RIFF-caller-audio")
        if request.url.path.endswith("/chat/completions"):
            self.model_calls += 1
            if self.model_status != 200:
                return httpx.Response(self.model_status, text="upstream error")
            return httpx.Response(200, json={"choices": [{"message": {"content": self.model_content}}]})
        return httpx.Response(404, text="unknown route")


@pytest.fixture
def omni_settings(settings, tmp_path):
    knowledge = tmp_path / "company.md"
    knowledge.write_text("We run bookkeeping and tax filing for small businesses.", encoding="utf-8")
    return replace(
        settings,
        dry_run=False,
        openrouter_api_key="or-key",
        company_knowledge_path=str(knowledge),
    )


def _client(settings, providers):
    http = httpx.Client(transport=httpx.MockTransport(providers))
    return TestClient(create_app(settings, http_client=http))


def _lead(settings):
    with crm.connect(settings.db_path) as conn:
        return crm.add_lead(conn, "Asha", "+9779800000000")


def test_greet_records_the_answer(omni_settings):
    lead_id = _lead(omni_settings)
    client = _client(omni_settings, FakeProviders())

    response = client.post("/voice/greet", params={"lead_id": lead_id})

    assert response.status_code == 200
    assert "<Record" in response.text
    assert "<Gather" not in response.text


def test_model_turn_drives_status_and_ends_on_accept(omni_settings):
    lead_id = _lead(omni_settings)
    reply = json.dumps({"heard": "yes, tell me more", "reply": "Great, someone will call you.", "intent": "accept", "end_call": True})
    providers = FakeProviders(model_content=reply)
    client = _client(omni_settings, providers)

    response = _finish_turn(client, client.post(
        "/voice/respond",
        params={"lead_id": lead_id, "attempt": 1},
        data={"RecordingUrl": RECORDING},
    ))

    assert "Great, someone will call you." in response.text
    assert "<Hangup/>" in response.text
    assert providers.model_calls == 1
    with crm.connect(omni_settings.db_path) as conn:
        assert crm.get_lead(conn, lead_id)["status"] == "interested"
        heard = [i["body"] for i in crm.list_interactions(conn, lead_id) if i["direction"] == "in"]
    assert heard == ["yes, tell me more"]


def test_model_failure_falls_back_to_a_safe_question(omni_settings):
    lead_id = _lead(omni_settings)
    client = _client(omni_settings, FakeProviders(model_status=429))

    response = _finish_turn(client, client.post(
        "/voice/respond",
        params={"lead_id": lead_id, "attempt": 1},
        data={"RecordingUrl": RECORDING},
    ))

    assert response.status_code == 200
    assert "<Record" in response.text
    assert "didn" in response.text
    with crm.connect(omni_settings.db_path) as conn:
        assert crm.get_lead(conn, lead_id)["status"] == "new"


def test_silence_is_logged_and_not_sent_to_the_model(omni_settings):
    lead_id = _lead(omni_settings)
    providers = FakeProviders()
    client = _client(omni_settings, providers)

    response = _finish_turn(client, client.post("/voice/respond", params={"lead_id": lead_id, "attempt": 1}, data={}))

    assert response.status_code == 200
    assert providers.model_calls == 0
    with crm.connect(omni_settings.db_path) as conn:
        heard = [i["body"] for i in crm.list_interactions(conn, lead_id) if i["direction"] == "in"]
    assert heard == ["(no speech)"]


def test_inbound_call_creates_the_lead_and_records(omni_settings):
    client = _client(omni_settings, FakeProviders())

    response = client.post("/voice/inbound", data={"From": "+9779811111111"})

    assert response.status_code == 200
    assert "<Record" in response.text
    assert "inbound=1" in response.text
    with crm.connect(omni_settings.db_path) as conn:
        lead = crm.find_lead_by_phone(conn, "+9779811111111")
    assert lead is not None


def test_inbound_call_from_unknown_caller_hangs_up(omni_settings):
    client = _client(omni_settings, FakeProviders())

    response = client.post("/voice/inbound", data={"From": "client:anonymous"})

    assert "<Hangup/>" in response.text
    assert "<Record" not in response.text


def _whatsapp_payload(wa_id, text):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "Asha"}}],
                            "messages": [{"from": wa_id, "id": "wamid.Q", "type": "text", "text": {"body": text}}],
                        }
                    }
                ]
            }
        ]
    }


def test_whatsapp_question_gets_a_model_answer(omni_settings):
    sent = []

    def handler(request):
        if "graph.facebook.com" in str(request.url):
            sent.append(json.loads(request.content)["text"]["body"])
            return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "Our bookkeeping starts at a fixed monthly fee."}}]})
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = TestClient(create_app(omni_settings, http_client=http))

    response = client.post("/whatsapp/webhook", json=_whatsapp_payload("9779800000000", "What do you charge for bookkeeping?"))

    assert response.status_code == 200
    assert sent == ["Our bookkeeping starts at a fixed monthly fee."]
    with crm.connect(omni_settings.db_path) as conn:
        lead = crm.find_lead_by_phone(conn, "+9779800000000")
        outbound = [i["body"] for i in crm.list_interactions(conn, lead["id"]) if i["direction"] == "out"]
    assert outbound == ["Our bookkeeping starts at a fixed monthly fee."]


def test_whatsapp_falls_back_to_canned_reply_when_model_fails(omni_settings):
    sent = []

    def handler(request):
        if "graph.facebook.com" in str(request.url):
            sent.append(json.loads(request.content)["text"]["body"])
            return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})
        return httpx.Response(429, text="rate limited")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = TestClient(create_app(omni_settings, http_client=http))

    response = client.post("/whatsapp/webhook", json=_whatsapp_payload("9779800000000", "hello"))

    assert response.status_code == 200
    assert sent and "member of our team" in sent[0]


def test_speech_mode_sends_the_transcript_to_the_text_model(omni_settings):
    lead_id = _lead(omni_settings)
    speech_settings = replace(omni_settings, voice_input="speech")
    reply = json.dumps({"heard": "tell me more", "reply": "We handle bookkeeping and tax filing.", "intent": "unclear", "end_call": False})
    seen = {}

    def handler(request):
        if request.url.path.endswith("/chat/completions"):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
        return httpx.Response(404)

    client = TestClient(create_app(speech_settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    response = _finish_turn(client, client.post(
        "/voice/respond",
        params={"lead_id": lead_id, "attempt": 1},
        data={"SpeechResult": "tell me more"},
    ))

    assert "We handle bookkeeping and tax filing." in response.text
    assert "<Gather" in response.text
    assert "tell me more" in seen["body"]["messages"][1]["content"]
    with crm.connect(speech_settings.db_path) as conn:
        heard = [i["body"] for i in crm.list_interactions(conn, lead_id) if i["direction"] == "in"]
    assert heard == ["tell me more"]
