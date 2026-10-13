"""Follow-up phone calls through Twilio.

Twilio fetches TwiML from our own webhooks (see server.py). Each turn: Twilio says a
line, listens, posts the transcript back to us, and we pick the next line. That
turn-taking loop is the whole voice agent for now.
"""

from xml.sax.saxutils import escape

from voice_crm.config import require
from voice_crm.exceptions import VoiceCallError
from voice_crm.logger import get_logger
from voice_crm.transport import get_bytes, post_json

log = get_logger("voice")

TWILIO_CALLS_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
MAX_ATTEMPTS = 2

ACCEPT_LINE = "Great, thank you. Someone from our team will reach out with the next steps. Goodbye."
DECLINE_LINE = "Understood, sorry for the interruption. We won't call you again. Goodbye."
UNCLEAR_LINE = "Sorry, I didn't catch that. Are you interested in hearing more? Please say yes or no."
GIVE_UP_LINE = "Thanks for your time. Goodbye."
SILENCE_LINE = "Sorry, I didn't hear anything. Goodbye."


def opening_line(name):
    return f"Hi {name}, this is a quick follow-up call from our team. Are you still interested in talking with us?"


def greet_url(base, lead_id):
    return f"{base}/voice/greet?lead_id={lead_id}"


INBOUND_GREETING = "Thanks for calling. How can I help you today?"


def turn_url(base, lead_id, attempt, inbound=False):
    suffix = "&inbound=1" if inbound else ""
    return f"{base}/voice/respond?lead_id={lead_id}&attempt={attempt}{suffix}"


def reply_for(detected, attempt):
    """Return (line to say, whether the call ends after it)."""
    if detected == "accept":
        return ACCEPT_LINE, True
    if detected == "decline":
        return DECLINE_LINE, True
    if attempt >= MAX_ATTEMPTS:
        return GIVE_UP_LINE, True
    return UNCLEAR_LINE, False


def _attr(value):
    return escape(value, {'"': "&quot;"})


def say_and_listen(line, action_url):
    return (
        "<Response>"
        f"<Say>{escape(line)}</Say>"
        f'<Gather input="speech" action="{_attr(action_url)}" '
        'method="POST" speechTimeout="auto" timeout="5"/>'
        f"<Say>{escape(SILENCE_LINE)}</Say>"
        "<Hangup/>"
        "</Response>"
    )


def say_and_record(line, action_url):
    return (
        "<Response>"
        f"<Say>{escape(line)}</Say>"
        f'<Record action="{_attr(action_url)}" method="POST" maxLength="20" timeout="3" '
        'playBeep="false" recordingFormat="wav"/>'
        f"<Say>{escape(SILENCE_LINE)}</Say>"
        "<Hangup/>"
        "</Response>"
    )


WAIT_LINE = "One moment, please."
SLOW_LINE = "Sorry, that took too long. Someone from our team will follow up. Goodbye."


def wait_url(base, lead_id, turn, attempt, inbound, waits):
    suffix = "&inbound=1" if inbound else ""
    return f"{base}/voice/wait?lead_id={lead_id}&turn={turn}&attempt={attempt}&waits={waits}{suffix}"


def say_and_wait(line, wait_action):
    return f'<Response><Say>{escape(line)}</Say><Redirect method="POST">{escape(wait_action)}</Redirect></Response>'


def pause_and_redirect(wait_action):
    return f'<Response><Pause length="1"/><Redirect method="POST">{escape(wait_action)}</Redirect></Response>'


def say_and_hang_up(line):
    return f"<Response><Say>{escape(line)}</Say><Hangup/></Response>"


def download_recording(settings, recording_url, client=None):
    if settings.dry_run:
        log.info("[dry-run] would download recording %s", recording_url)
        return b""

    sid = require(settings.twilio_account_sid, "TWILIO_ACCOUNT_SID")
    token = require(settings.twilio_auth_token, "TWILIO_AUTH_TOKEN")
    url = recording_url if recording_url.endswith(".wav") else f"{recording_url}.wav"
    audio = get_bytes(url, VoiceCallError, client=client, auth=(sid, token))
    log.info("Downloaded caller recording (%s bytes)", len(audio))
    return audio


def status_url(base, lead_id):
    return f"{base}/voice/status?lead_id={lead_id}"


def place_call(settings, to_phone, callback_url, status_callback=None, client=None):
    if settings.dry_run:
        log.info("[dry-run] would call %s, TwiML from %s", to_phone, callback_url)
        return "dry-run"

    sid = require(settings.twilio_account_sid, "TWILIO_ACCOUNT_SID")
    token = require(settings.twilio_auth_token, "TWILIO_AUTH_TOKEN")
    from_number = require(settings.twilio_from_number, "TWILIO_FROM_NUMBER")
    data = {"To": to_phone, "From": from_number, "Url": callback_url}
    if status_callback:
        data.update({"StatusCallback": status_callback, "StatusCallbackEvent": "completed"})

    response = post_json(
        TWILIO_CALLS_URL.format(sid=sid),
        VoiceCallError,
        client=client,
        data=data,
        auth=(sid, token),
    )
    try:
        call_sid = response["sid"]
    except (KeyError, TypeError) as exc:
        raise VoiceCallError(f"Unexpected Twilio response: {response}") from exc

    log.info("Placed call sid=%s to %s", call_sid, to_phone)
    return call_sid
