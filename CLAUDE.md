# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A WhatsApp and voice follow-up agent on top of a small CRM. It is a portfolio project, so keep it readable over clever. The README has the user-facing description and the list of known gaps.

## Commands

Python 3.11+ managed with uv. Dependencies live in `pyproject.toml`; the dev group holds pytest.

```bash
uv sync                          # install everything, including dev
uv run pytest                    # full suite (74 tests, no network)
uv run pytest tests/test_followups.py::test_run_followups_sends_whatsapp_then_stops_until_due
uv run voice-crm init-db
uv run voice-crm serve --port 8000
```

There is no linter configured yet.

## Architecture

The rule that shapes everything: pure functions that take values and return values, with side effects only at the edges. The edges are `whatsapp.py`, `voice.py` (HTTP to providers), `crm.py` (SQLite), and `server.py` (HTTP in).

Four flows go through the code:

- **Inbound WhatsApp:** `server.py` `/whatsapp/webhook` → `whatsapp.parse_inbound` → `_handle_whatsapp_message` → `crm` logs the message → `intent.read_intent` sets lead status → `whatsapp.send_text` replies.
- **Outbound follow-up:** `followups.run_followups` lists leads, `plan_followup` decides a channel from the interaction history (pure, time passed in), then schedules a row in `followups`. It then executes due rows: WhatsApp intro via `whatsapp.send_text`, or a call via `voice.place_call` pointing Twilio at `/voice/greet`.
- **Voice call:** Twilio fetches TwiML from `/voice/greet`, then posts each spoken answer to `/voice/respond?lead_id=&attempt=`. `voice.reply_for` picks the next line and whether to hang up. The call is turn-based, not streaming.
- **CLI:** `cli.py` dispatches subcommands and sets up logging once. Commands open a connection with `crm.connect`, which also creates the schema.

- **HubSpot sync:** `sync.sync_pending` pushes every lead (create or update the contact) and every interaction with `synced = 0` (a note on the contact). It runs at the end of `run-followups` and after each WhatsApp webhook. It only writes back `leads.hubspot_id` and `interactions.synced`, so it is safe to re-run. It is skipped when `HUBSPOT_ACCESS_TOKEN` is empty.

State lives in SQLite (`leads`, `interactions`, `followups`). HubSpot is a mirror, never the source of truth for the agent. `followups.plan_followup` reads the `interactions` history, so new follow-up rules belong there, not in the executor.

## Conventions

- **Dry run is the default.** `Settings.dry_run` is true unless `DRY_RUN=0`. Every provider function checks it first and only logs. Keep it that way when adding a new outbound action.
- **Logging:** use `get_logger("<part>")` from `logger.py`. All loggers sit under the `voice_crm` tree, which `setup_logging` configures once. No `print` for diagnostics; `print` is only for CLI output.
- **Errors:** every expected failure is a subclass of `VoiceCRMError` in `exceptions.py`. Provider failures raise `WhatsAppError` or `VoiceCallError`. The webhook loop catches per message so one bad message does not fail the batch, because Meta retries non-200 responses.
- **Tests** mock HTTP with `httpx.MockTransport` by passing a `client=` argument. Keep provider functions accepting that argument.
- **Code style:** plain names, short functions, comments only for non-obvious reasons, no boilerplate docstrings. The code should read as if one person wrote it.

## Not done yet

Twilio and Meta request signatures are not verified, so do not expose the webhooks without adding that check. The repo is not a git repository yet.
