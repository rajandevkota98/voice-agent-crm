import json
import sqlite3
from dataclasses import replace

import httpx

from voice_crm import crm, hubspot
from voice_crm.sync import sync_pending


class FakeHubSpot:
    """Records every request and answers the handful of endpoints sync uses."""

    def __init__(self, existing_contact_id=None, fail_search=False):
        self.calls = []
        self.existing_contact_id = existing_contact_id
        self.fail_search = fail_search

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path.endswith("/contacts/search"):
            if self.fail_search:
                return httpx.Response(401, text="unauthorized")
            results = [{"id": self.existing_contact_id}] if self.existing_contact_id else []
            return httpx.Response(200, json={"results": results})
        if path == "/crm/v3/objects/contacts":
            return httpx.Response(201, json={"id": "501"})
        if path.startswith("/crm/v3/objects/contacts/"):
            return httpx.Response(200, json={})
        if path == "/crm/v3/objects/notes":
            return httpx.Response(201, json={"id": "n1"})
        return httpx.Response(404, text="unknown route")

    def paths(self, method):
        return [path for m, path, _ in self.calls if m == method]


def _live(settings):
    return replace(settings, dry_run=False, hubspot_token="hs-token")


def _client(fake):
    return httpx.Client(transport=httpx.MockTransport(fake))


def _seed(conn):
    lead_id = crm.add_lead(conn, "Asha Gurung", "+9779800000000")
    crm.log_interaction(conn, lead_id, "whatsapp", "out", "Hi Asha")
    return lead_id


def test_first_sync_creates_contact_and_note(settings, conn):
    lead_id = _seed(conn)
    fake = FakeHubSpot()

    stats = sync_pending(conn, _live(settings), client=_client(fake))

    assert stats == {"contacts": 1, "notes": 1, "failed": 0}
    assert crm.get_lead(conn, lead_id)["hubspot_id"] == "501"
    assert crm.unsynced_interactions(conn) == []

    contact_body = next(body for m, path, body in fake.calls if path == "/crm/v3/objects/contacts")
    assert contact_body["properties"]["firstname"] == "Asha"
    assert contact_body["properties"]["lastname"] == "Gurung"
    assert contact_body["properties"]["hs_lead_status"] == "NEW"

    note_body = next(body for m, path, body in fake.calls if path == "/crm/v3/objects/notes")
    assert note_body["properties"]["hs_note_body"] == "[whatsapp out] Hi Asha"
    assert note_body["associations"][0]["to"]["id"] == "501"


def test_second_sync_only_updates_and_never_duplicates(settings, conn):
    _seed(conn)
    live = _live(settings)
    sync_pending(conn, live, client=_client(FakeHubSpot()))

    fake = FakeHubSpot()
    sync_pending(conn, live, client=_client(fake))

    assert fake.paths("POST") == []
    assert fake.paths("PATCH") == ["/crm/v3/objects/contacts/501"]


def test_existing_hubspot_contact_is_reused(settings, conn):
    lead_id = _seed(conn)
    fake = FakeHubSpot(existing_contact_id="777")

    sync_pending(conn, _live(settings), client=_client(fake))

    assert crm.get_lead(conn, lead_id)["hubspot_id"] == "777"
    assert "/crm/v3/objects/contacts" not in fake.paths("POST")
    assert fake.paths("PATCH") == ["/crm/v3/objects/contacts/777"]


def test_dry_run_makes_no_calls(settings, conn):
    _seed(conn)
    dry = replace(settings, dry_run=True, hubspot_token="hs-token")

    def fail_if_called(request):
        raise AssertionError("dry run must not call HubSpot")

    stats = sync_pending(conn, dry, client=httpx.Client(transport=httpx.MockTransport(fail_if_called)))

    assert stats == {"contacts": 0, "notes": 0, "failed": 0}
    assert len(crm.unsynced_interactions(conn)) == 1


def test_missing_token_skips_sync(settings, conn):
    _seed(conn)

    stats = sync_pending(conn, replace(settings, dry_run=False, hubspot_token=None))

    assert stats == {"contacts": 0, "notes": 0, "failed": 0}


def test_api_error_is_counted_and_does_not_stop_the_run(settings, conn):
    _seed(conn)
    fake = FakeHubSpot(fail_search=True)

    stats = sync_pending(conn, _live(settings), client=_client(fake))

    assert stats["failed"] >= 1
    assert stats["contacts"] == 0
    assert len(crm.unsynced_interactions(conn)) == 1


def test_split_name_and_status_mapping():
    assert hubspot.split_name("Asha Gurung Rai") == ("Asha", "Gurung Rai")
    assert hubspot.split_name("Bikash") == ("Bikash", "")
    assert hubspot.LEAD_STATUS["interested"] == "IN_PROGRESS"


def test_connect_upgrades_an_old_database(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE leads (id INTEGER PRIMARY KEY, name TEXT, phone TEXT UNIQUE, status TEXT,
                            created_at TEXT, updated_at TEXT);
        CREATE TABLE interactions (id INTEGER PRIMARY KEY, lead_id INTEGER, channel TEXT,
                                   direction TEXT, body TEXT, created_at TEXT);
        """
    )
    old.close()

    upgraded = crm.connect(path)
    columns = {
        table: {row["name"] for row in upgraded.execute(f"PRAGMA table_info({table})")}
        for table in ("leads", "interactions")
    }
    upgraded.close()

    assert "hubspot_id" in columns["leads"]
    assert "synced" in columns["interactions"]
