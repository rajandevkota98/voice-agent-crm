"""One place for the HTTP plumbing shared by the WhatsApp, Twilio, HubSpot, and OpenRouter clients."""

import httpx

TIMEOUT_SECONDS = 10.0


def _send(method, url, error_cls, client, **request_kwargs):
    owns_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = http.request(method, url, **request_kwargs)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise error_cls(f"{url} returned {exc.response.status_code}: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise error_cls(f"{url} request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()


def request_json(method, url, error_cls, client=None, **request_kwargs):
    response = _send(method, url, error_cls, client, **request_kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise error_cls(f"{url} did not return JSON") from exc


def post_json(url, error_cls, client=None, **request_kwargs):
    return request_json("POST", url, error_cls, client=client, **request_kwargs)


def get_bytes(url, error_cls, client=None, **request_kwargs):
    return _send("GET", url, error_cls, client, **request_kwargs).content
