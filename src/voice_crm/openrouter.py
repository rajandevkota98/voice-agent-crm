"""OpenRouter chat completions with audio input. The omni model hears the caller directly."""

import base64

from voice_crm.config import require
from voice_crm.exceptions import LLMError
from voice_crm.logger import get_logger
from voice_crm.transport import post_json

log = get_logger("llm")

BASE_URL = "https://openrouter.ai/api/v1"
DRY_RUN_REPLY = '{"heard": "(dry run)", "reply": "Thanks, we will be in touch.", "intent": "unclear", "end_call": false}'


def ask_with_audio(settings, audio, audio_format, system_prompt, client=None):
    if settings.dry_run:
        log.info("[dry-run] would ask %s about %s bytes of audio", settings.omni_model, len(audio))
        return DRY_RUN_REPLY

    key = require(settings.openrouter_api_key, "OPENROUTER_API_KEY")
    payload = {
        "model": settings.omni_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": audio_format},
                    }
                ],
            },
        ],
    }
    response = post_json(
        f"{BASE_URL}/chat/completions",
        LLMError,
        client=client,
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response from {settings.omni_model}: {response}") from exc
    if not isinstance(text, str):
        raise LLMError(f"{settings.omni_model} returned no text content")

    log.info("%s answered with %s characters", settings.omni_model, len(text))
    return text


def ask_text(settings, system_prompt, messages, client=None):
    if settings.dry_run:
        log.info("[dry-run] would ask %s a text question (%s messages)", settings.text_model, len(messages))
        return "Thanks for your message. A member of our team will get back to you soon."

    key = require(settings.openrouter_api_key, "OPENROUTER_API_KEY")
    payload = {
        "model": settings.text_model,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
    }
    response = post_json(
        f"{BASE_URL}/chat/completions",
        LLMError,
        client=client,
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response from {settings.text_model}: {response}") from exc
    if not isinstance(text, str) or not text.strip():
        raise LLMError(f"{settings.text_model} returned no text")
    return text.strip()
