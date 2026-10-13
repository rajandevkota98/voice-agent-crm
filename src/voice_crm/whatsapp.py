"""WhatsApp Cloud API: outbound messages and inbound webhook payloads.

Free-form text only works inside 24 hours of the customer's last message. A first
message to a lead has to be a pre-approved template.
"""

from dataclasses import dataclass

from voice_crm.config import require
from voice_crm.exceptions import WhatsAppError
from voice_crm.logger import get_logger
from voice_crm.transport import post_json

log = get_logger("whatsapp")

GRAPH_URL = "https://graph.facebook.com/v20.0"

REPLIES = {
    "accept": "Great, thanks! Our team will reach out shortly with the next steps.",
    "decline": "Understood, thanks for letting us know. We won't contact you again.",
    "unclear": "Thanks for your message. A member of our team will get back to you soon.",
}


@dataclass(frozen=True)
class InboundMessage:
    wa_id: str
    name: str | None
    message_id: str
    text: str


def send_text(settings, to_phone, body, client=None):
    if settings.dry_run:
        log.info("[dry-run] would send WhatsApp to %s: %r", to_phone, body)
        return "dry-run"
    return _post_message(settings, to_phone, {"type": "text", "text": {"body": body}}, client)


def send_template(settings, to_phone, template_name, language, client=None):
    if settings.dry_run:
        log.info("[dry-run] would send WhatsApp template %s to %s", template_name, to_phone)
        return "dry-run"
    template = {"name": template_name, "language": {"code": language}}
    return _post_message(settings, to_phone, {"type": "template", "template": template}, client)


def _post_message(settings, to_phone, content, client):
    token = require(settings.whatsapp_token, "WHATSAPP_TOKEN")
    phone_id = require(settings.whatsapp_phone_id, "WHATSAPP_PHONE_ID")
    payload = {"messaging_product": "whatsapp", "to": to_phone.lstrip("+"), **content}

    response = post_json(
        f"{GRAPH_URL}/{phone_id}/messages",
        WhatsAppError,
        client=client,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        message_id = response["messages"][0]["id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise WhatsAppError(f"Unexpected send response for {to_phone}: {response}") from exc

    log.info("Sent WhatsApp %s message id=%s to %s", content["type"], message_id, to_phone)
    return message_id


def parse_inbound(payload):
    messages = []
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                names = {c["wa_id"]: c.get("profile", {}).get("name") for c in value.get("contacts", [])}
                for raw in value.get("messages", []):
                    if raw.get("type") != "text":
                        log.info("Skipping %s message id=%s", raw.get("type"), raw.get("id"))
                        continue
                    sender = raw["from"]
                    messages.append(
                        InboundMessage(
                            wa_id=sender,
                            name=names.get(sender),
                            message_id=raw["id"],
                            text=raw["text"]["body"],
                        )
                    )
    except (AttributeError, KeyError, TypeError) as exc:
        raise WhatsAppError(f"Malformed webhook payload: {exc}") from exc
    return messages


def verify_webhook(params, verify_token):
    if verify_token and params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == verify_token:
        log.info("WhatsApp webhook verified")
        return params.get("hub.challenge", "")
    raise WhatsAppError("Webhook verification failed: wrong mode or verify token")
