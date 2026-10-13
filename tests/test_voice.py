from urllib.parse import parse_qs

import httpx
import pytest

from voice_crm import voice
from voice_crm.config import Settings
from voice_crm.exceptions import ConfigError, VoiceCallError


def test_twiml_escapes_spoken_text():
    xml = voice.say_and_hang_up("Tom & Jerry <3")

    assert "Tom &amp; Jerry &lt;3" in xml
    assert "<Hangup/>" in xml


def test_listen_turn_points_back_at_us():
    xml = voice.say_and_listen("Hello", "https://example.test/voice/respond?lead_id=7&attempt=2")

    assert 'action="https://example.test/voice/respond?lead_id=7&amp;attempt=2"' in xml
    assert 'input="speech"' in xml


@pytest.mark.parametrize(
    ("detected", "attempt", "ends"),
    [("accept", 1, True), ("decline", 1, True), ("unclear", 1, False), ("unclear", 2, True)],
)
def test_reply_for_ends_the_call_when_it_should(detected, attempt, ends):
    _, ends_call = voice.reply_for(detected, attempt)

    assert ends_call is ends


def test_place_call_is_a_dry_run_by_default():
    assert voice.place_call(Settings(dry_run=True), "+9779800000000", "https://example.test/x") == "dry-run"


def test_place_call_posts_form_data_to_twilio(settings):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(201, json={"sid": "CA999"})

    live = Settings(
        dry_run=False,
        twilio_account_sid="AC123",
        twilio_auth_token="twilio-token",
        twilio_from_number="+15550001111",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))

    call_sid = voice.place_call(live, "+9779800000000", "https://example.test/voice/greet?lead_id=1", client=client)

    assert call_sid == "CA999"
    assert "/Accounts/AC123/Calls.json" in seen["url"]
    assert seen["form"]["To"] == ["+9779800000000"]
    assert seen["form"]["From"] == ["+15550001111"]
    assert seen["form"]["Url"] == ["https://example.test/voice/greet?lead_id=1"]


def test_place_call_wraps_twilio_errors():
    live = Settings(
        dry_run=False,
        twilio_account_sid="AC123",
        twilio_auth_token="twilio-token",
        twilio_from_number="+15550001111",
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401, text="unauthorized")))

    with pytest.raises(VoiceCallError, match="401"):
        voice.place_call(live, "+9779800000000", "https://example.test/x", client=client)


def test_place_call_without_credentials_is_a_config_error():
    with pytest.raises(ConfigError, match="TWILIO_ACCOUNT_SID"):
        voice.place_call(Settings(dry_run=False), "+9779800000000", "https://example.test/x")
