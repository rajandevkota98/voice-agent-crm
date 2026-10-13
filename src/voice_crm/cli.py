"""Command line entry point: `voice-crm <command>`."""

import argparse
from contextlib import closing

import uvicorn

from voice_crm import crm, followups, sync
from voice_crm.config import load_dotenv, load_settings
from voice_crm.exceptions import VoiceCRMError
from voice_crm.logger import get_logger, setup_logging
from voice_crm.server import create_app

log = get_logger("cli")


def build_parser():
    parser = argparse.ArgumentParser(prog="voice-crm", description="WhatsApp and voice follow-ups for your leads.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init-db", help="create the database tables")

    add = commands.add_parser("add-lead", help="add a lead")
    add.add_argument("name")
    add.add_argument("phone", help="E.164 format, e.g. +9779812345678")

    listing = commands.add_parser("list-leads", help="print leads")
    listing.add_argument("--status", choices=crm.LEAD_STATUSES)

    commands.add_parser("run-followups", help="plan and send the follow-ups that are due")
    commands.add_parser("sync-hubspot", help="push leads and notes to HubSpot")

    serve = commands.add_parser("serve", help="run the webhook server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def _init_db(settings, args):
    with closing(crm.connect(settings.db_path)):
        pass
    print(f"Database ready at {settings.db_path}")
    return 0


def _add_lead(settings, args):
    with closing(crm.connect(settings.db_path)) as conn:
        lead_id = crm.add_lead(conn, args.name, args.phone)
    print(f"Added lead {lead_id}: {args.name} ({crm.normalize_phone(args.phone)})")
    return 0


def _list_leads(settings, args):
    with closing(crm.connect(settings.db_path)) as conn:
        leads = crm.list_leads(conn, status=args.status)
    for lead in leads:
        print(f"{lead['id']:>4}  {lead['status']:<15} {lead['phone']:<16} {lead['name']}")
    if not leads:
        print("No leads yet.")
    return 0


def _run_followups(settings, args):
    with closing(crm.connect(settings.db_path)) as conn:
        stats = followups.run_followups(conn, settings)
        sync.sync_pending(conn, settings)
    print(f"Planned {stats['planned']}, sent {stats['sent']}, failed {stats['failed']}")
    return 1 if stats["failed"] else 0


def _sync_hubspot(settings, args):
    with closing(crm.connect(settings.db_path)) as conn:
        stats = sync.sync_pending(conn, settings)
    print(f"Synced {stats['contacts']} contacts and {stats['notes']} notes, failed {stats['failed']}")
    return 1 if stats["failed"] else 0


def _serve(settings, args):
    log.info("Serving on http://%s:%s", args.host, args.port)
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_config=None)
    return 0


COMMANDS = {
    "init-db": _init_db,
    "add-lead": _add_lead,
    "list-leads": _list_leads,
    "run-followups": _run_followups,
    "sync-hubspot": _sync_hubspot,
    "serve": _serve,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    load_dotenv()
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_file)

    if settings.dry_run:
        log.info("DRY_RUN is on. Nothing will be sent to WhatsApp or Twilio.")

    try:
        return COMMANDS[args.command](settings, args)
    except VoiceCRMError as exc:
        log.error("%s failed: %s", args.command, exc)
        return 1
