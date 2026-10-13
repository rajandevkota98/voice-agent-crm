"""Settings come from environment variables and are read once at startup.

DRY_RUN is on unless you turn it off, so nothing reaches WhatsApp or Twilio by accident.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from voice_crm.exceptions import ConfigError


@dataclass(frozen=True)
class Settings:
    db_path: str = "crm.db"
    log_level: str = "INFO"
    log_file: str | None = None
    dry_run: bool = True
    public_base_url: str | None = None
    whatsapp_token: str | None = None
    whatsapp_phone_id: str | None = None
    whatsapp_verify_token: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    hubspot_token: str | None = None
    openrouter_api_key: str | None = None
    omni_model: str = "openai/gpt-audio-mini"
    company_knowledge_path: str = "company_knowledge.md"
    whatsapp_template_name: str = "hello_world"
    whatsapp_template_language: str = "en_US"
    sheet_webhook_url: str | None = None
    voice_input: str = "record"
    text_model: str = "~anthropic/claude-opus-latest"


def _optional(env, key):
    value = env.get(key, "").strip()
    return value or None


def load_settings(env=None):
    env = os.environ if env is None else env
    base_url = _optional(env, "PUBLIC_BASE_URL")
    return Settings(
        db_path=env.get("CRM_DB_PATH", "crm.db"),
        log_level=env.get("LOG_LEVEL", "INFO").strip().upper(),
        log_file=_optional(env, "LOG_FILE"),
        dry_run=env.get("DRY_RUN", "1").strip().lower() not in ("0", "false", "no"),
        public_base_url=base_url.rstrip("/") if base_url else None,
        whatsapp_token=_optional(env, "WHATSAPP_TOKEN"),
        whatsapp_phone_id=_optional(env, "WHATSAPP_PHONE_ID"),
        whatsapp_verify_token=_optional(env, "WHATSAPP_VERIFY_TOKEN"),
        twilio_account_sid=_optional(env, "TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_optional(env, "TWILIO_AUTH_TOKEN"),
        twilio_from_number=_optional(env, "TWILIO_FROM_NUMBER"),
        hubspot_token=_optional(env, "HUBSPOT_ACCESS_TOKEN"),
        openrouter_api_key=_optional(env, "OPENROUTER_API_KEY"),
        omni_model=env.get("OMNI_MODEL", "openai/gpt-audio-mini").strip(),
        company_knowledge_path=env.get("COMPANY_KNOWLEDGE_PATH", "company_knowledge.md").strip(),
        whatsapp_template_name=env.get("WHATSAPP_TEMPLATE_NAME", "hello_world").strip(),
        whatsapp_template_language=env.get("WHATSAPP_TEMPLATE_LANGUAGE", "en_US").strip(),
        sheet_webhook_url=_optional(env, "GOOGLE_SHEET_WEBHOOK_URL"),
        voice_input=env.get("VOICE_INPUT", "record").strip().lower(),
        text_model=env.get("TEXT_MODEL", "~anthropic/claude-opus-latest").strip(),
    )


def load_dotenv(path=".env"):
    """Read KEY=VALUE lines into os.environ. Variables already set in the shell win."""
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require(value, name):
    if not value:
        raise ConfigError(f"{name} is not set. Add it to your environment or .env file.")
    return value
