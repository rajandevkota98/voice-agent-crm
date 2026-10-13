# voice-crm

A small follow-up agent for sales leads. It keeps leads in a CRM, talks to them on WhatsApp, and calls the ones who go quiet. When a call is answered, a simple turn-by-turn voice agent asks whether they are still interested and records the answer.

It is deliberately small: one SQLite file, two webhooks, and a command line. Every outbound action goes through a dry-run switch first.

## What it does

- **CRM:** leads with a status (`new`, `contacted`, `interested`, `not_interested`, `won`, `lost`), a log of every message and call, and scheduled follow-ups.
- **WhatsApp:** sends the first message to new leads through the WhatsApp Cloud API, receives replies on a webhook, and answers them with a short, rule-based reply.
- **Voice:** when a lead goes quiet for two days, or says they are interested, places a Twilio call. The call asks a yes or no question, listens, and ends with the right line for the answer.
- **Follow-up rules:** written as plain functions in `followups.py`. A lead who said no is never contacted again.

## Setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env          # leave DRY_RUN=1 for now
uv run voice-crm init-db
uv run voice-crm add-lead "Asha Gurung" +9779800000000
uv run voice-crm run-followups
```

With `DRY_RUN=1` the follow-up run logs what it would send and changes nothing outside the database.

## Going live

1. Create a WhatsApp Cloud API app in Meta and fill in `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, and `WHATSAPP_VERIFY_TOKEN`.
2. Create a Twilio account with a voice-capable number and fill in the `TWILIO_*` values.
3. Expose the server with a public HTTPS URL (for example `ngrok http 8000`) and set `PUBLIC_BASE_URL` to it.
4. Point the Meta webhook at `https://<your-url>/whatsapp/webhook`.
5. Start the server with `uv run voice-crm serve --port 8000`.
6. Set `DRY_RUN=0` and run `uv run voice-crm run-followups` on a schedule (cron is enough).

## HubSpot CRM

HubSpot is the client-facing CRM. SQLite stays the agent's working store, and the sync copies leads and conversation notes into HubSpot. If HubSpot is down, the agent keeps working and the next sync catches up.

1. Create a free HubSpot account.
2. In HubSpot, go to Settings → Integrations → Private Apps and create an app. Give it read and write scopes for contacts and notes.
3. Copy the access token into `HUBSPOT_ACCESS_TOKEN` in `.env`.
4. Run `uv run voice-crm sync-hubspot`. The follow-up run and the WhatsApp webhook also sync.

Leads map to HubSpot contacts, matched by phone. Our statuses map to HubSpot's lead status. Each WhatsApp or call message is saved as a note on the contact.

## Commands

| Command | What it does |
| --- | --- |
| `init-db` | Creates the SQLite tables. |
| `add-lead NAME PHONE` | Adds a lead. Phone must be E.164. |
| `list-leads [--status S]` | Prints leads. |
| `run-followups` | Plans new follow-ups, sends the ones that are due, then syncs to HubSpot. |
| `sync-hubspot` | Pushes leads and unsynced notes to HubSpot. |
| `serve [--host H --port P]` | Runs the webhook server. |

## Layout

```
src/voice_crm/
  cli.py        command line entry point
  server.py     FastAPI routes: WhatsApp webhook and Twilio voice turns
  followups.py  the follow-up rules, and the loop that runs them
  crm.py        SQLite tables and queries
  sync.py       pushes leads and notes from SQLite to HubSpot
  hubspot.py    HubSpot contacts and notes API client
  whatsapp.py   WhatsApp Cloud API client and webhook parsing
  voice.py      Twilio client and TwiML for the call
  intent.py     keyword intent: accept, decline, or unclear
  config.py     settings from environment variables
  logger.py     the one logger setup for the project
  transport.py  shared HTTP helpers for the provider clients
```

The code is written as functions that take values and return values. Network calls and database writes sit at the edges, in `whatsapp.py`, `voice.py`, `crm.py`, and `server.py`.

## Tests

```bash
uv run pytest
```

The provider clients are tested against `httpx.MockTransport`, so the suite makes no real calls.

## Known gaps

- Twilio and Meta request signatures are not checked yet. Anyone who can reach the webhook URLs can post to them. Check the `X-Twilio-Signature` and `X-Hub-Signature-256` headers before going live.
- Intent detection is keyword matching. It is easy to read, but it misses phrasing like "maybe next month". Swap in a classifier in `intent.py` when that matters.
- The voice agent is turn-based (speech in, speech out, using Twilio's built-in speech recognition and voice). It is not a streaming, low-latency conversation.
- There is one sales owner and no user accounts or dashboard.
- HubSpot sync is one-way (SQLite → HubSpot). Changes made directly in HubSpot are not read back.
- Sync updates every lead on every run. That's fine for hundreds of leads, not tens of thousands.
- HubSpot's rate limits (about 100 requests per 10 seconds on the free tier) are not handled with backoff yet. A 429 is logged as a failed item.
- The HubSpot scopes and association type IDs were written from HubSpot's docs but have not been run against a live account yet. Check the first sync's logs.
