"""HubSpot CRM client: contacts and notes. Only sync.py calls this module.

Auth is a private app token (HUBSPOT_ACCESS_TOKEN) with contacts and notes scopes.
"""

from datetime import datetime

from voice_crm.exceptions import HubSpotError
from voice_crm.transport import request_json

API_URL = "https://api.hubapi.com"

# Contact-to-note association type that HubSpot defines out of the box.
NOTE_TO_CONTACT = 202

# HubSpot's standard lead status property, mapped from our own statuses.
LEAD_STATUS = {
    "new": "NEW",
    "contacted": "ATTEMPTED_TO_CONTACT",
    "interested": "IN_PROGRESS",
    "not_interested": "UNQUALIFIED",
    "won": "OPEN_DEAL",
    "lost": "UNQUALIFIED",
}


def split_name(full_name):
    first, _, last = full_name.strip().partition(" ")
    return first, last.strip()


def to_epoch_ms(stamp):
    return str(int(datetime.fromisoformat(stamp).timestamp() * 1000))


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _call(method, path, token, client, payload=None):
    return request_json(
        method,
        f"{API_URL}{path}",
        HubSpotError,
        client=client,
        headers=_headers(token),
        json=payload,
    )


def _contact_properties(name, phone, status):
    first, last = split_name(name)
    return {
        "firstname": first,
        "lastname": last,
        "phone": phone,
        "hs_lead_status": LEAD_STATUS[status],
    }


def find_contact_id(token, phone, client=None):
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "phone", "operator": "EQ", "value": phone}]}],
        "properties": ["phone"],
        "limit": 1,
    }
    results = _call("POST", "/crm/v3/objects/contacts/search", token, client, payload).get("results", [])
    return results[0]["id"] if results else None


def create_contact(token, name, phone, status, client=None):
    payload = {"properties": _contact_properties(name, phone, status)}
    return _call("POST", "/crm/v3/objects/contacts", token, client, payload)["id"]


def update_contact(token, contact_id, name, phone, status, client=None):
    payload = {"properties": _contact_properties(name, phone, status)}
    _call("PATCH", f"/crm/v3/objects/contacts/{contact_id}", token, client, payload)


def create_note(token, contact_id, body, stamp, client=None):
    payload = {
        "properties": {"hs_note_body": body, "hs_timestamp": to_epoch_ms(stamp)},
        "associations": [
            {
                "to": {"id": contact_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_TO_CONTACT}],
            }
        ],
    }
    return _call("POST", "/crm/v3/objects/notes", token, client, payload)["id"]
