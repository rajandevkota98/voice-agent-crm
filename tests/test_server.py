from fastapi.testclient import TestClient

from voice_crm import crm
from voice_crm.server import create_app


def _client(settings):
    return TestClient(create_app(settings))


def _inbound(wa_id, text):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id, "profile": {"name": "Asha"}}],
                            "messages": [{"from": wa_id, "id": "wamid.X", "type": "text", "text": {"body": text}}],
                        }
                    }
                ]
            }
        ]
    }


def test_webhook_verification_echoes_challenge(settings):
    client = _client(settings)

    response = client.get(
        "/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"},
    )

    assert response.status_code == 200
    assert response.text == "42"


def test_webhook_verification_rejects_wrong_token(settings):
    client = _client(settings)

    response = client.get(
        "/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "42"},
    )

    assert response.status_code == 403


def test_inbound_message_creates_lead_and_marks_interest(settings):
    client = _client(settings)

    response = client.post("/whatsapp/webhook", json=_inbound("9779800000000", "yes, call me"))

    assert response.status_code == 200
    with crm.connect(settings.db_path) as conn:
        lead = crm.find_lead_by_phone(conn, "+9779800000000")
        assert lead["name"] == "Asha"
        assert lead["status"] == "interested"
        channels = [(i["direction"], i["body"]) for i in crm.list_interactions(conn, lead["id"])]
    assert channels[0] == ("in", "yes, call me")
    assert channels[1][0] == "out"


def test_bad_webhook_payload_is_a_400(settings):
    client = _client(settings)

    response = client.post("/whatsapp/webhook", json={"entry": "nope"})

    assert response.status_code == 400


def test_voice_greet_then_decline_ends_the_call(settings):
    with crm.connect(settings.db_path) as conn:
        lead_id = crm.add_lead(conn, "Asha", "+9779800000000")
    client = _client(settings)

    greet = client.post("/voice/greet", params={"lead_id": lead_id})
    assert greet.status_code == 200
    assert "Hi Asha" in greet.text
    assert "<Gather" in greet.text

    respond = client.post(
        "/voice/respond",
        params={"lead_id": lead_id, "attempt": 1},
        data={"SpeechResult": "not interested"},
    )
    assert respond.status_code == 200
    assert "<Hangup/>" in respond.text
    assert "<Gather" not in respond.text

    with crm.connect(settings.db_path) as conn:
        assert crm.get_lead(conn, lead_id)["status"] == "not_interested"


def test_voice_unclear_answer_asks_again_then_gives_up(settings):
    with crm.connect(settings.db_path) as conn:
        lead_id = crm.add_lead(conn, "Asha", "+9779800000000")
    client = _client(settings)

    first = client.post("/voice/respond", params={"lead_id": lead_id, "attempt": 1}, data={"SpeechResult": "hmm"})
    second = client.post("/voice/respond", params={"lead_id": lead_id, "attempt": 2}, data={"SpeechResult": "hmm"})

    assert "<Gather" in first.text
    assert "<Gather" not in second.text
    assert "Thanks for your time" in second.text
