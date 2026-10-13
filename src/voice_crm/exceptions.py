"""Errors this project raises on purpose. Catch VoiceCRMError to handle any of them."""


class VoiceCRMError(Exception):
    """Base class for every expected failure in the project."""


class ConfigError(VoiceCRMError):
    """A setting needed for the requested action is missing."""


class CRMError(VoiceCRMError):
    """Bad input, a missing record, or a rejected database write."""


class WhatsAppError(VoiceCRMError):
    """A WhatsApp API call failed, or a webhook payload was malformed."""


class VoiceCallError(VoiceCRMError):
    """A Twilio call request failed."""


class HubSpotError(VoiceCRMError):
    """A HubSpot API call failed or returned something we did not expect."""


class LLMError(VoiceCRMError):
    """The OpenRouter model call failed or returned something we could not read."""


class SheetError(VoiceCRMError):
    """Writing a row to the Google Sheet failed."""
