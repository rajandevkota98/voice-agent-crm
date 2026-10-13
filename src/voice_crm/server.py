"""FastAPI app: the WhatsApp webhook and the Twilio voice turns."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from voice_crm import crm, intent, openrouter, prompts, sync, voice, whatsapp, wrapup
from voice_crm.config import Settings, require
from voice_crm.exceptions import CRMError, VoiceCRMError, WhatsAppError
from voice_crm.logger import get_logger

log = get_logger("server")


def _twiml(xml):
    return Response(content=xml, media_type="application/xml")


def _require_lead(conn, lead_id):
    lead = crm.get_lead(conn, lead_id)
    if lead is None:
        raise CRMError(f"No lead with id {lead_id}")
    return lead


def _lead_id_from_call(conn, form):
    for field in ("From", "To"):
        try:
            lead = crm.find_lead_by_phone(conn, str(form.get(field) or ""))
        except CRMError:
            continue
        if lead:
            return lead["id"]
    return None


def _listen(settings, line, action_url):
    """Ask the caller something. Record the answer when the omni model is on, otherwise use Twilio speech."""
    if settings.openrouter_api_key and settings.voice_input == "record":
        return voice.say_and_record(line, action_url)
    return voice.say_and_listen(line, action_url)


def _model_turn(settings, lead, recording_url, speech, attempt, http_client, inbound=False):
    """Ask the model what to say next. Returns (heard, line, intent, ends_call).

    Audio mode sends the caller's recording to the omni model. Speech mode sends
    Twilio's transcript to a text model. Any provider failure falls back to a safe
    line, so the caller never hears dead air.
    """
    heard_nothing = not recording_url if settings.voice_input == "record" else not speech
    if heard_nothing:
        line, ends_call = voice.reply_for("unclear", attempt)
        return "(no speech)", line, "unclear", ends_call

    try:
        knowledge = prompts.load_company_knowledge(settings.company_knowledge_path)
        system = prompts.voice_system_prompt(lead, knowledge, inbound=inbound)
        if settings.voice_input == "record":
            audio = voice.download_recording(settings, recording_url, client=http_client)
            raw = openrouter.ask_with_audio(settings, audio, "wav", system, client=http_client)
        else:
            raw = openrouter.ask_text(
                settings,
                system,
                [{"role": "user", "content": f"The caller just said: {speech}"}],
                client=http_client,
            )
        turn = prompts.parse_turn(raw)
    except VoiceCRMError as exc:
        log.error("Omni turn failed for lead id=%s, using the fallback line: %s", lead["id"], exc)
        line, ends_call = voice.reply_for("unclear", attempt)
        return "(unavailable)", line, "unclear", ends_call

    if turn.intent == "unclear" and attempt >= voice.MAX_ATTEMPTS:
        return turn.heard, voice.GIVE_UP_LINE, "unclear", True
    return turn.heard, turn.reply, turn.intent, turn.end_call


def _whatsapp_reply(conn, settings, lead_id, lead, detected, http_client):
    """Answer with the model when it's configured, otherwise use the canned line for the intent."""
    if not settings.openrouter_api_key:
        return whatsapp.REPLIES[detected]
    try:
        history = [
            i for i in crm.list_interactions(conn, lead_id) if i["channel"] == "whatsapp"
        ][-10:]
        messages = [
            {"role": "user" if i["direction"] == "in" else "assistant", "content": i["body"]}
            for i in history
        ]
        knowledge = prompts.load_company_knowledge(settings.company_knowledge_path)
        return openrouter.ask_text(
            settings,
            prompts.whatsapp_system_prompt(lead, knowledge),
            messages,
            client=http_client,
        )
    except VoiceCRMError as exc:
        log.error("WhatsApp model reply failed for lead id=%s, using the canned line: %s", lead["id"], exc)
        return whatsapp.REPLIES[detected]


# Model replies run in the background so Twilio never waits on them. Twilio asks
# /voice/wait every second until the reply is ready, up to MAX_WAITS checks.
MAX_WAITS = 12
_TURN_POOL = ThreadPoolExecutor(max_workers=4)
_TURNS = {}


def _start_turn(settings, lead, form, attempt, http_client, inbound):
    turn_key = uuid.uuid4().hex
    recording = str(form.get("RecordingUrl") or "")
    speech = str(form.get("SpeechResult") or "")
    _TURNS[turn_key] = _TURN_POOL.submit(
        _model_turn, settings, lead, recording, speech, attempt, http_client, inbound
    )
    return turn_key


def _handle_whatsapp_message(conn, settings, message, http_client=None):
    phone = crm.normalize_phone(f"+{message.wa_id}")
    lead = crm.find_lead_by_phone(conn, phone)
    if lead is None:
        lead_id = crm.add_lead(conn, message.name or "Unknown", phone)
        log.info("Created lead id=%s from an inbound WhatsApp message", lead_id)
    else:
        lead_id = lead["id"]

    crm.log_interaction(conn, lead_id, "whatsapp", "in", message.text)

    detected = intent.read_intent(message.text)
    if detected in intent.STATUS_FOR_INTENT:
        crm.set_status(conn, lead_id, intent.STATUS_FOR_INTENT[detected])

    lead = crm.get_lead(conn, lead_id)
    reply = _whatsapp_reply(conn, settings, lead_id, lead, detected, http_client)
    whatsapp.send_text(settings, phone, reply, client=http_client)
    crm.log_interaction(conn, lead_id, "whatsapp", "out", reply)


def create_app(settings: Settings, http_client=None) -> FastAPI:
    app = FastAPI(title="voice-crm")

    @app.exception_handler(VoiceCRMError)
    async def handle_project_error(request: Request, exc: VoiceCRMError):
        log.error("%s %s failed: %s", request.method, request.url.path, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/whatsapp/webhook")
    async def whatsapp_verify(request: Request):
        try:
            challenge = whatsapp.verify_webhook(dict(request.query_params), settings.whatsapp_verify_token)
        except WhatsAppError as exc:
            log.warning("Rejected webhook verification: %s", exc)
            raise HTTPException(status_code=403, detail="verification failed")
        return PlainTextResponse(challenge)

    @app.post("/whatsapp/webhook")
    async def whatsapp_inbound(request: Request):
        try:
            messages = whatsapp.parse_inbound(await request.json())
        except (ValueError, WhatsAppError) as exc:
            log.warning("Rejected webhook payload: %s", exc)
            raise HTTPException(status_code=400, detail="bad payload")

        with closing(crm.connect(settings.db_path)) as conn:
            for message in messages:
                # Meta retries anything that is not a 200, so one bad message
                # must not fail the whole batch.
                try:
                    _handle_whatsapp_message(conn, settings, message, http_client)
                except VoiceCRMError as exc:
                    log.error("Could not handle message id=%s: %s", message.message_id, exc)
            sync.sync_pending(conn, settings)
        return {"received": len(messages)}

    @app.post("/voice/greet")
    async def voice_greet(lead_id: int):
        base = require(settings.public_base_url, "PUBLIC_BASE_URL")
        with closing(crm.connect(settings.db_path)) as conn:
            lead = _require_lead(conn, lead_id)
            line = voice.opening_line(lead["name"])
            crm.log_interaction(conn, lead_id, "call", "out", line)
        return _twiml(_listen(settings, line, voice.turn_url(base, lead_id, attempt=1)))

    @app.post("/voice/inbound")
    async def voice_inbound(request: Request):
        """Twilio posts here when someone calls your number."""
        base = require(settings.public_base_url, "PUBLIC_BASE_URL")
        form = await request.form()
        try:
            caller = crm.normalize_phone(str(form.get("From") or ""))
        except CRMError:
            log.warning("Inbound call with no usable caller number, hanging up")
            return _twiml(voice.say_and_hang_up("Sorry, we could not identify your number. Goodbye."))

        with closing(crm.connect(settings.db_path)) as conn:
            lead = crm.find_lead_by_phone(conn, caller)
            lead_id = lead["id"] if lead else crm.add_lead(conn, "Caller", caller)
            crm.log_interaction(conn, lead_id, "call", "in", "(inbound call)")
            crm.log_interaction(conn, lead_id, "call", "out", voice.INBOUND_GREETING)
        return _twiml(_listen(settings, voice.INBOUND_GREETING, voice.turn_url(base, lead_id, 1, inbound=True)))

    @app.post("/voice/status")
    async def voice_status(request: Request, lead_id: int | None = None):
        """Twilio posts here when a call ends. Outbound calls pass lead_id; inbound calls match the caller's number."""
        form = await request.form()
        if form.get("CallStatus") != "completed":
            return Response(status_code=204)

        with closing(crm.connect(settings.db_path)) as conn:
            target = lead_id or _lead_id_from_call(conn, form)
            if target is None:
                log.warning("Call completed but no lead matches it, nothing to log")
                return Response(status_code=204)
            wrapup.finish_call(conn, settings, target, client=http_client)
        return Response(status_code=204)

    @app.post("/voice/respond")
    async def voice_respond(request: Request, lead_id: int, attempt: int = 1, inbound: int = 0):
        base = require(settings.public_base_url, "PUBLIC_BASE_URL")
        form = await request.form()
        is_inbound = bool(inbound)

        with closing(crm.connect(settings.db_path)) as conn:
            lead = _require_lead(conn, lead_id)
            if settings.openrouter_api_key:
                turn_key = _start_turn(settings, lead, form, attempt, http_client, is_inbound)
                wait = voice.wait_url(base, lead_id, turn_key, attempt, is_inbound, waits=0)
                return _twiml(voice.say_and_wait(voice.WAIT_LINE, wait))

            heard = str(form.get("SpeechResult") or "")
            detected = intent.read_intent(heard)
            line, ends_call = voice.reply_for(detected, attempt)
            crm.log_interaction(conn, lead_id, "call", "in", heard or "(no speech)")
            if detected in intent.STATUS_FOR_INTENT:
                crm.set_status(conn, lead_id, intent.STATUS_FOR_INTENT[detected])
            crm.log_interaction(conn, lead_id, "call", "out", line)

        if ends_call:
            return _twiml(voice.say_and_hang_up(line))
        return _twiml(_listen(settings, line, voice.turn_url(base, lead_id, attempt + 1, inbound=is_inbound)))

    @app.post("/voice/wait")
    async def voice_wait(lead_id: int, turn: str, attempt: int = 1, waits: int = 0, inbound: int = 0):
        base = require(settings.public_base_url, "PUBLIC_BASE_URL")
        is_inbound = bool(inbound)
        future = _TURNS.get(turn)
        if future is None:
            log.error("Unknown turn %s for lead id=%s", turn, lead_id)
            return _twiml(voice.say_and_hang_up(voice.GIVE_UP_LINE))

        if not future.done():
            if waits < MAX_WAITS:
                return _twiml(voice.pause_and_redirect(voice.wait_url(base, lead_id, turn, attempt, is_inbound, waits + 1)))
            _TURNS.pop(turn, None)
            log.error("Model reply for lead id=%s took too long", lead_id)
            return _twiml(voice.say_and_hang_up(voice.SLOW_LINE))

        _TURNS.pop(turn, None)
        try:
            heard, line, detected, ends_call = future.result()
        except Exception as exc:
            log.error("Model turn crashed for lead id=%s, using the fallback line: %s", lead_id, exc)
            heard = "(unavailable)"
            line, ends_call = voice.reply_for("unclear", attempt)
            detected = "unclear"

        with closing(crm.connect(settings.db_path)) as conn:
            _require_lead(conn, lead_id)
            crm.log_interaction(conn, lead_id, "call", "in", heard or "(no speech)")
            if detected in intent.STATUS_FOR_INTENT:
                crm.set_status(conn, lead_id, intent.STATUS_FOR_INTENT[detected])
            crm.log_interaction(conn, lead_id, "call", "out", line)

        if ends_call:
            return _twiml(voice.say_and_hang_up(line))
        return _twiml(_listen(settings, line, voice.turn_url(base, lead_id, attempt + 1, inbound=is_inbound)))

    return app
