"""What the voice agent is told, and how its reply is read back."""

import json
from dataclasses import dataclass
from pathlib import Path

from voice_crm.exceptions import ConfigError, LLMError
from voice_crm.logger import get_logger

log = get_logger("prompts")

INTENTS = ("accept", "decline", "unclear")


@dataclass(frozen=True)
class Turn:
    heard: str
    reply: str
    intent: str
    end_call: bool


def load_company_knowledge(path):
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"Company knowledge file not found at {path}. Copy company_knowledge.md and fill it in.")
    return file.read_text(encoding="utf-8").strip()


def voice_system_prompt(lead, knowledge, inbound=False):
    if inbound:
        situation = f"A caller ({lead['name']}) phoned the company and you are answering. "
    else:
        situation = f"You are on a follow-up call with {lead['name']}. "
    return (
        "You are the phone assistant for the company described below. "
        f"{situation}"
        "Listen to the caller's audio, then answer the way a helpful person would on the phone. "
        "Keep each reply to two short sentences. "
        "Never invent prices, dates, or promises that are not in the company information.\n\n"
        "Reply with JSON only, in this shape:\n"
        '{"heard": "what the caller said", "reply": "what you say next", '
        '"intent": "accept" | "decline" | "unclear", "end_call": true | false}\n'
        "Use accept if they want to continue, decline if they are not interested, and unclear otherwise. "
        "Set end_call to true only when the call should finish.\n\n"
        f"Company information:\n{knowledge}"
    )


def parse_turn(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model reply was not JSON: {raw[:200]!r}") from exc
    if not isinstance(data, dict):
        raise LLMError(f"Model reply was not a JSON object: {raw[:200]!r}")

    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise LLMError("Model reply had no 'reply' text")

    intent = data.get("intent")
    if intent not in INTENTS:
        log.warning("Model returned unknown intent %r, treating it as unclear", intent)
        intent = "unclear"

    return Turn(
        heard=str(data.get("heard") or ""),
        reply=reply.strip(),
        intent=intent,
        end_call=bool(data.get("end_call")),
    )


def whatsapp_system_prompt(lead, knowledge):
    return (
        "You are the WhatsApp assistant for the company described below. "
        f"You are chatting with {lead['name']}. "
        "Answer their questions using only the company information. "
        "Keep replies short, friendly, and in plain text, with no formatting or emojis. "
        "If the answer isn't in the company information, say that a team member will follow up. "
        "Never invent prices, dates, or promises.\n\n"
        f"Company information:\n{knowledge}"
    )
