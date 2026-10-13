# Architecture

voice-crm is one Python service with three entry points: the webhook server (`voice-crm serve`), the follow-up job (`voice-crm run-followups`), and the CRM sync (`voice-crm sync-hubspot`). SQLite is the working store. HubSpot and a Google Sheet receive copies.

## Components

```mermaid
flowchart LR
    subgraph Providers
        META[WhatsApp Cloud API<br/>Meta]
        TWIL[Twilio Voice]
        OR[OpenRouter<br/>text and audio models]
        HS[HubSpot CRM]
        GS[Google Sheet<br/>via Apps Script]
    end

    subgraph App["voice-crm (Python)"]
        SRV["server.py<br/>FastAPI webhooks"]
        FU["followups.py<br/>plan and send"]
        CLI["cli.py<br/>commands"]
        WRAP["wrapup.py<br/>after each call"]
        SYNC["sync.py<br/>push to HubSpot"]
        PROMPT["prompts.py<br/>system prompts, reply parsing"]
        INTENT["intent.py<br/>keyword fallback"]
        DB[(SQLite<br/>leads, interactions, followups)]
    end

    KB[/company_knowledge.md/]

    META -- inbound messages --> SRV
    SRV -- replies --> META
    TWIL -- call webhooks --> SRV
    SRV -- TwiML --> TWIL
    CLI --> FU
    FU -- first contact template / outbound call --> META
    FU -- outbound call --> TWIL
    SRV --> DB
    FU --> DB
    SRV -- model calls --> OR
    FU -- model calls --> OR
    PROMPT --> KB
    SRV --> PROMPT
    SRV --> INTENT
    SRV -- call ended --> WRAP
    CLI -- sync-hubspot --> SYNC
    WRAP --> SYNC
    WRAP --> GS
    SYNC --> HS
    SYNC --> DB
```

## Inbound WhatsApp message

```mermaid
sequenceDiagram
    participant Customer
    participant Meta as WhatsApp Cloud API
    participant App as server.py
    participant DB as SQLite
    participant LLM as OpenRouter (text model)

    Customer->>Meta: sends a message
    Meta->>App: POST /whatsapp/webhook
    App->>DB: find or create lead, log inbound message
    App->>LLM: question + company info + last 10 messages
    LLM-->>App: answer (or error: canned reply used)
    App->>Meta: send text reply
    App->>DB: log outbound reply
    App->>App: sync to HubSpot (if configured)
    App-->>Meta: 200 OK
```

## Outbound follow-up call

```mermaid
sequenceDiagram
    participant Job as run-followups
    participant DB as SQLite
    participant Twilio
    participant App as server.py
    participant LLM as OpenRouter
    participant Wrap as wrapup.py

    Job->>DB: plan follow-ups (rules in followups.py)
    Job->>Twilio: create call (Url = /voice/greet)
    Twilio->>App: POST /voice/greet
    App-->>Twilio: greeting + listen (Gather, or Record in audio mode)
    Twilio->>App: POST /voice/respond (caller's speech or recording)
    App->>LLM: reply in background
    App-->>Twilio: "One moment" + Redirect to /voice/wait
    loop until ready (max 12 checks)
        Twilio->>App: POST /voice/wait
        App-->>Twilio: Pause + Redirect, or the reply
    end
    App->>DB: log what was heard and said, update status
    Twilio->>App: POST /voice/status (CallStatus=completed)
    App->>Wrap: finish call
    Wrap->>DB: read transcript
    Wrap-->>App: notes to HubSpot, row to Google Sheet
```

## Design decisions

- **SQLite is the source of truth.** HubSpot and the sheet are write-only mirrors. If either is down, the agent keeps working and the next sync catches up.
- **Dry run is the default.** `DRY_RUN=1` stops all outbound WhatsApp, Twilio, HubSpot, and sheet writes. Model calls return canned text.
- **Model replies never block Twilio.** Twilio gives a webhook only a few seconds. The reply is computed in a background thread, and Twilio polls `/voice/wait` until it's ready.
- **Two voice input modes.** `VOICE_INPUT=record` sends the caller's audio to an omni model. `VOICE_INPUT=speech` uses Twilio's speech recognition and a text model. Speech mode works on trial accounts.
- **Failures fall back, not fail.** A model error gives a safe spoken or written line. One bad WhatsApp message does not block the batch. A HubSpot outage does not stop the sheet row.
- **Functional style.** Logic is plain functions over values. Network calls and database writes sit at the edges: `whatsapp.py`, `voice.py`, `openrouter.py`, `hubspot.py`, `sheets.py`, `crm.py`, and `server.py`.

## Configuration

Every setting comes from environment variables (`.env`). See `.env.example`. The app loads `.env` on startup, and variables already set in the shell take precedence.
