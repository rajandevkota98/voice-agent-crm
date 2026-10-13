import json

import httpx
import pytest

from voice_crm.config import Settings
from voice_crm.exceptions import ConfigError, WhatsAppError
from voice_crm.whatsapp import parse_inbound, send_template, send_text, verify_webhook

INBOUND_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "value": {
                        "contacts": [{"wa_id": "9779800000000", "profile": {"name": "Asha"}}],
                        "messages": [
                            {"from": "9779800000000", "id": "wamid.A", "type": "text", "text": {"body": "yes"}},
                            {"from": "9779800000000", "id": "wamid.B", "type": "image"},
                        ],
                    }
                }
            ]
        }
    ]
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_send_text_is_a_dry_run_by_default(settings):
    dry = Settings(dry_run=True)

    assert send_text(dry, "+9779800000000", "hello") == "dry-run"


def test_send_text_posts_to_graph_api(settings):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.SENT"}]})

    live = Settings(dry_run=False, whatsapp_token="wa-token", whatsapp_phone_id="12345")

    message_id = send_text(live, "+9779800000000", "hello", client=_client(handler))

    assert message_id == "wamid.SENT"
    assert seen["url"].endswith("/12345/messages")
    assert seen["auth"] == "Bearer wa-token"
    assert seen["body"]["to"] == "9779800000000"
    assert seen["body"]["text"]["body"] == "hello"


def test_send_text_wraps_api_errors(settings):
    live = Settings(dry_run=False, whatsapp_token="wa-token", whatsapp_phone_id="12345")
    client = _client(lambda request: httpx.Response(400, text="bad token"))

    with pytest.raises(WhatsAppError, match="400"):
        send_text(live, "+9779800000000", "hello", client=client)


def test_send_text_without_token_is_a_config_error():
    live = Settings(dry_run=False, whatsapp_phone_id="12345")

    with pytest.raises(ConfigError, match="WHATSAPP_TOKEN"):
        send_text(live, "+9779800000000", "hello")


def test_parse_inbound_keeps_text_and_skips_other_types():
    messages = parse_inbound(INBOUND_PAYLOAD)

    assert len(messages) == 1
    assert messages[0].wa_id == "9779800000000"
    assert messages[0].name == "Asha"
    assert messages[0].text == "yes"


def test_parse_inbound_rejects_malformed_payload():
    with pytest.raises(WhatsAppError):
        parse_inbound({"entry": "not-a-list"})


def test_verify_webhook_returns_challenge_for_matching_token():
    params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1234"}

    assert verify_webhook(params, "verify-me") == "1234"


def test_verify_webhook_rejects_wrong_token():
    params = {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1234"}

    with pytest.raises(WhatsAppError):
        verify_webhook(params, "verify-me")


def test_send_template_uses_template_payload():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.T"}]})

    live = Settings(dry_run=False, whatsapp_token="wa-token", whatsapp_phone_id="12345")

    message_id = send_template(live, "+9779800000000", "hello_world", "en_US", client=_client(handler))

    assert message_id == "wamid.T"
    assert seen["body"]["type"] == "template"
    assert seen["body"]["template"] == {"name": "hello_world", "language": {"code": "en_US"}}
