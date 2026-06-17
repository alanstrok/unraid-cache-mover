#!/usr/bin/env python3
"""
Plex sign-in helper ("Sign in with Plex") for the cache-mover plugin.

Implements the Plex PIN-based OAuth flow plus server auto-discovery so the
settings page can obtain an X-Plex-Token without the user hunting for one,
and pre-fill the primary + additional servers.

The flow (driven by plex_auth.php / the settings-page JavaScript):
  1. pin       -> POST https://plex.tv/api/v2/pins?strong=true
                  returns {id, code}; we build the app.plex.tv/auth URL.
  2. poll      -> GET  https://plex.tv/api/v2/pins/<id>
                  returns authToken once the user authorises in the popup.
  3. resources -> GET  https://plex.tv/api/v2/resources
                  lists the account's Plex Media Servers with a usable
                  connection (scheme/host/port) and per-server accessToken.

Everything is emitted as a single JSON object on stdout so the PHP proxy can
relay it verbatim. Network errors are reported as {"error": "..."}.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

PLEX_TV = "https://plex.tv"
AUTH_APP = "https://app.plex.tv/auth"
DEFAULT_PRODUCT = "Unraid Cache Mover"


def gen_client_id() -> str:
    """A stable per-install identifier; persisted in settings once issued."""
    return "unraid-cache-mover-" + uuid.uuid4().hex


def build_auth_url(client_id: str, code: str, product: str = DEFAULT_PRODUCT,
                   forward_url: str = "") -> str:
    """Build the app.plex.tv/auth#? URL the user opens to authorise."""
    params = [
        ("clientID", client_id),
        ("code", code),
        ("context[device][product]", product),
    ]
    if forward_url:
        params.append(("forwardUrl", forward_url))
    return f"{AUTH_APP}#?" + urllib.parse.urlencode(params)


def _headers(client_id: str, token: Optional[str] = None,
             product: str = DEFAULT_PRODUCT) -> Dict[str, str]:
    h = {
        "Accept": "application/json",
        "X-Plex-Product": product,
        "X-Plex-Client-Identifier": client_id,
    }
    if token:
        h["X-Plex-Token"] = token
    return h


def _request(method: str, url: str, client_id: str, token: Optional[str] = None,
             product: str = DEFAULT_PRODUCT, timeout: float = 10.0) -> Any:
    data = b"" if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=_headers(client_id, token, product))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))


def create_pin(client_id: str, product: str = DEFAULT_PRODUCT) -> Dict[str, Any]:
    resp = _request("POST", f"{PLEX_TV}/api/v2/pins?strong=true",
                    client_id, product=product)
    return {
        "id": resp.get("id"),
        "code": resp.get("code"),
        "client_id": client_id,
        "auth_url": build_auth_url(client_id, resp.get("code") or "", product),
    }


def poll_pin(pin_id: str, client_id: str,
             product: str = DEFAULT_PRODUCT) -> Dict[str, Any]:
    url = f"{PLEX_TV}/api/v2/pins/{urllib.parse.quote(str(pin_id))}"
    resp = _request("GET", url, client_id, product=product)
    return {"auth_token": resp.get("authToken") or ""}


def _pick_connection(conns: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Choose the best connection for a LAN-resident mover.

    Preference: local before remote, non-relay before relay, and http before
    https (a raw-IP https connection can't be cert-verified, and Plex exposes
    http on the LAN by default). Lower score sorts first.
    """
    if not conns:
        return None

    def score(c: Dict[str, Any]):
        return (
            0 if c.get("local") else 1,
            1 if c.get("relay") else 0,
            1 if (c.get("protocol") == "https") else 0,
        )

    return sorted(conns, key=score)[0]


def parse_resources(data: Any) -> List[Dict[str, Any]]:
    """Turn the /api/v2/resources payload into a flat list of servers."""
    if isinstance(data, dict):
        items = data.get("MediaContainer", {}).get("Device", [])
    else:
        items = data or []
    servers: List[Dict[str, Any]] = []
    for d in items:
        provides = (d.get("provides") or "")
        if "server" not in [p.strip() for p in provides.split(",")]:
            continue
        conn = _pick_connection(d.get("connections") or [])
        if not conn:
            continue
        servers.append({
            "name": d.get("name") or "Plex Server",
            "client_identifier": d.get("clientIdentifier") or "",
            "owned": bool(d.get("owned")),
            "access_token": d.get("accessToken") or "",
            "scheme": conn.get("protocol") or "http",
            "host": conn.get("address") or "",
            "port": str(conn.get("port") or ""),
            "local": bool(conn.get("local")),
            "relay": bool(conn.get("relay")),
            "uri": conn.get("uri") or "",
        })
    return servers


def list_resources(token: str, client_id: str,
                   product: str = DEFAULT_PRODUCT) -> Dict[str, Any]:
    url = (f"{PLEX_TV}/api/v2/resources"
           "?includeHttps=1&includeRelay=1")
    resp = _request("GET", url, client_id, token=token, product=product)
    return {"servers": parse_resources(resp)}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["pin", "poll", "resources"])
    ap.add_argument("--client-id", dest="client_id", default="")
    ap.add_argument("--id", dest="pin_id", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--product", default=DEFAULT_PRODUCT)
    args = ap.parse_args(argv)

    client_id = args.client_id or gen_client_id()

    try:
        if args.action == "pin":
            out = create_pin(client_id, args.product)
        elif args.action == "poll":
            if not args.pin_id:
                raise ValueError("missing --id")
            out = poll_pin(args.pin_id, client_id, args.product)
        else:  # resources
            if not args.token:
                raise ValueError("missing --token")
            out = list_resources(args.token, client_id, args.product)
    except Exception as exc:  # noqa: BLE001
        json.dump({"error": str(exc)}, sys.stdout)
        sys.stdout.write("\n")
        return 0

    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
