import base64
import json
from dataclasses import replace

import httpx
import pytest

from voice_crm import openrouter, prompts, voice
from voice_crm.config import Settings
from voice_crm.exceptions import ConfigError, LLMError, VoiceCallError


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _live(**overrides):
    base = Settings(dry_run=False, openrouter_api_key="or-key", omni_model="openai/gpt-audio-mini")
    return replace(base, **overrides)


def _completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_ask_with_audio_sends_base64_audio_to_the_model():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return _completion('{"reply": "Sure", "intent": "accept", "end_call": false}')

    text = openrouter.ask_with_audio(_live(), b"RIFF-fake-audio", "wav", "be brief", client=_client(handler))

    assert text.startswith('{"reply"')
    assert seen["auth"] == "Bearer or-key"
    assert seen["body"]["model"] == "openai/gpt-audio-mini"
    audio_part = seen["body"]["messages"][1]["content"][0]
    assert audio_part["type"] == "input_audio"
    assert audio_part["input_audio"]["format"] == "wav"
    assert base64.b64decode(audio_part["input_audio"]["data"]) == b"RIFF-fake-audio"


def test_ask_with_audio_dry_run_makes_no_call():
    def fail_if_called(request):
        raise AssertionError("dry run must not call OpenRouter")

    reply = openrouter.ask_with_audio(
        Settings(dry_run=True, openrouter_api_key="or-key"), b"", "wav", "x", client=_client(fail_if_called)
    )

    assert json.loads(reply)["intent"] == "unclear"


def test_ask_with_audio_wraps_http_errors():
    client = _client(lambda request: httpx.Response(429, text="rate limited"))

    with pytest.raises(LLMError, match="429"):
        openrouter.ask_with_audio(_live(), b"x", "wav", "x", client=client)


def test_ask_with_audio_without_key_is_a_config_error():
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        openrouter.ask_with_audio(Settings(dry_run=False), b"x", "wav", "x")


def test_parse_turn_reads_plain_json():
    turn = prompts.parse_turn('{"heard": "yes", "reply": "Great.", "intent": "accept", "end_call": true}')

    assert turn == prompts.Turn(heard="yes", reply="Great.", intent="accept", end_call=True)


def test_parse_turn_strips_code_fences():
    raw = '```json\n{"heard": "no", "reply": "Okay.", "intent": "decline", "end_call": true}\n```'

    assert prompts.parse_turn(raw).intent == "decline"


def test_parse_turn_treats_unknown_intent_as_unclear():
    turn = prompts.parse_turn('{"reply": "Hmm.", "intent": "maybe later", "end_call": false}')

    assert turn.intent == "unclear"


def test_parse_turn_rejects_non_json():
    with pytest.raises(LLMError):
        prompts.parse_turn("Sure, happy to help!")


def test_parse_turn_rejects_missing_reply():
    with pytest.raises(LLMError):
        prompts.parse_turn('{"intent": "accept"}')


def test_download_recording_fetches_wav_with_twilio_auth():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, content=b"RIFF-caller")

    live = _live(twilio_account_sid="AC123", twilio_auth_token="tok")
    audio = voice.download_recording(live, "https://api.twilio.com/2010-04-01/Accounts/AC123/Recordings/RE1", client=_client(handler))

    assert audio == b"RIFF-caller"
    assert seen["url"].endswith("/Recordings/RE1.wav")
    assert seen["auth"].startswith("Basic ")


def test_download_recording_wraps_errors():
    live = _live(twilio_account_sid="AC123", twilio_auth_token="tok")
    client = _client(lambda request: httpx.Response(404, text="gone"))

    with pytest.raises(VoiceCallError, match="404"):
        voice.download_recording(live, "https://api.twilio.com/x/Recordings/RE1", client=client)


def test_say_and_record_points_the_recording_at_us():
    xml = voice.say_and_record("Hello", "https://example.test/voice/respond?lead_id=3&attempt=1")

    assert "<Record" in xml
    assert 'recordingFormat="wav"' in xml
    assert 'action="https://example.test/voice/respond?lead_id=3&amp;attempt=1"' in xml


def test_inbound_prompt_says_the_caller_phoned_in():
    lead = {"name": "Asha"}

    assert "phoned the company" in prompts.voice_system_prompt(lead, "info", inbound=True)
    assert "follow-up call" in prompts.voice_system_prompt(lead, "info")


def test_turn_url_carries_the_inbound_flag():
    assert "inbound=1" in voice.turn_url("https://example.test", 4, 1, inbound=True)
    assert "inbound" not in voice.turn_url("https://example.test", 4, 1)
