#!/bin/bash
#
# Offline tests for plex_auth.py pure functions (no network):
#   - build_auth_url: param order + URL-encoding of the Plex auth-app link
#   - parse_resources: filter to servers, pick the best connection, map fields
#   - _pick_connection: prefer local / non-relay / http for a LAN mover
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPTS="$REPO/src/usr/local/emhttp/plugins/cache-mover/scripts"

python3 - "$SCRIPTS" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import plex_auth as pa

fails = 0
def check(name, expect, actual):
    global fails
    if expect == actual:
        print("  OK  ", name)
    else:
        fails += 1
        print("  FAIL", name)
        print("      expected:", repr(expect))
        print("      actual:  ", repr(actual))

# --- build_auth_url ---
check("auth_url basic",
      "https://app.plex.tv/auth#?clientID=CID&code=CODE&context%5Bdevice%5D%5Bproduct%5D=Prod",
      pa.build_auth_url("CID", "CODE", "Prod"))
url = pa.build_auth_url("CID", "CODE", "P", "http://tower/x?y=1")
check("auth_url forwardUrl encoded", True,
      "forwardUrl=http%3A%2F%2Ftower%2Fx%3Fy%3D1" in url)

# --- parse_resources ---
resources = [
    {
        "name": "Tower", "clientIdentifier": "aaa", "provides": "server",
        "owned": True, "accessToken": "TOKEN_A",
        "connections": [
            {"protocol": "https", "address": "1.2.3.4", "port": 32400,
             "uri": "https://a.plex.direct:32400", "local": False, "relay": False},
            {"protocol": "http", "address": "192.168.1.10", "port": 32400,
             "uri": "http://192.168.1.10:32400", "local": True, "relay": False},
            {"protocol": "https", "address": "10.0.0.1", "port": 443,
             "uri": "https://relay.plex.direct:443", "local": False, "relay": True},
        ],
    },
    {
        "name": "Friend", "clientIdentifier": "bbb", "provides": "server",
        "owned": False, "accessToken": "TOKEN_B",
        "connections": [
            {"protocol": "https", "address": "5.6.7.8", "port": 32400,
             "uri": "https://b.plex.direct:32400", "local": False, "relay": False},
        ],
    },
    {
        "name": "MyPhone", "clientIdentifier": "ccc", "provides": "client,player",
        "owned": True,
        "connections": [
            {"protocol": "https", "address": "9.9.9.9", "port": 32400,
             "local": False, "relay": False},
        ],
    },
]

servers = pa.parse_resources(resources)
check("server count (clients excluded)", 2, len(servers))
check("tower host (local http chosen)", "192.168.1.10", servers[0]["host"])
check("tower scheme http",              "http",         servers[0]["scheme"])
check("tower port is string",           "32400",        servers[0]["port"])
check("tower local flag",               True,           servers[0]["local"])
check("tower owned",                    True,           servers[0]["owned"])
check("tower access_token",             "TOKEN_A",      servers[0]["access_token"])
check("friend host (only remote)",      "5.6.7.8",      servers[1]["host"])
check("friend scheme https",            "https",        servers[1]["scheme"])
check("friend owned false",             False,          servers[1]["owned"])
check("friend access_token",            "TOKEN_B",      servers[1]["access_token"])

# --- _pick_connection ordering ---
pick = pa._pick_connection(resources[0]["connections"])
check("pick local http over remote/relay/https",
      ("http", "192.168.1.10"), (pick["protocol"], pick["address"]))

# --- empty / alternate shapes ---
check("parse empty list", [], pa.parse_resources([]))
check("parse MediaContainer empty", 0, len(pa.parse_resources({"MediaContainer": {}})))
check("pick empty conns", None, pa._pick_connection([]))

print()
if fails:
    print("passed=?", "failed=%d" % fails)
    sys.exit(1)
print("All plex_auth.py tests passed.")
PY
