#!/usr/bin/env python3
"""
plex_webhook.py - tiny Plex webhook listener that triggers head-start fills.

Plex Pass POSTs a multipart/form-data body to this listener whenever
playback state changes. On media.play / media.resume we look up the
item's file path via the Plex API and hand it to cache_mover_fill, which
fills the sparse head in place (if the file is a tracked head).

This gives a sub-second, event-driven fill trigger - no polling, no cron.

Usage:
    plex_webhook.py --host 10.0.0.5 --port 32400 --token XXXX \
                    [--scheme http] [--listen 0.0.0.0] [--listen-port 8595] \
                    [--fill /path/to/cache_mover_fill]

Security note: this accepts unauthenticated POSTs on the chosen interface.
The only action it can cause is caching an existing library file (the file
path is resolved from Plex via your token, not from the request body), so
the blast radius is benign. Bind to a trusted interface/port anyway.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PLAY_EVENTS = ("media.play", "media.resume")


def parse_multipart_payload(body: bytes, content_type: str):
    """Extract the JSON string from the form field named 'payload'."""
    m = re.search(r"boundary=(.*)", content_type or "")
    if not m:
        return None
    boundary = m.group(1).strip().strip('"')
    delim = ("--" + boundary).encode()
    for part in body.split(delim):
        if b'name="payload"' not in part:
            continue
        idx = part.find(b"\r\n\r\n")
        if idx == -1:
            continue
        data = part[idx + 4:].rstrip(b"\r\n")
        return data.decode("utf-8", "replace")
    return None


def resolve_files(rating_key, args):
    """Return the list of Part file paths for a ratingKey via the Plex API."""
    url = "%s://%s:%s/library/metadata/%s" % (
        args.scheme, args.host, args.port, rating_key)
    req = urllib.request.Request(
        url, headers={"X-Plex-Token": args.token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
    except Exception as e:  # noqa: BLE001
        print("metadata fetch failed:", e, file=sys.stderr)
        return []
    out = []
    for meta in d.get("MediaContainer", {}).get("Metadata", []):
        for media in meta.get("Media", []):
            for part in media.get("Part", []):
                f = part.get("file")
                if f:
                    out.append(f)
    return out


def handle_payload(payload: str, args):
    """Parse a webhook payload and fire fills for play/resume events."""
    try:
        d = json.loads(payload)
    except Exception:  # noqa: BLE001
        return []
    if d.get("event") not in PLAY_EVENTS:
        return []
    rating_key = (d.get("Metadata") or {}).get("ratingKey")
    if not rating_key:
        return []
    files = resolve_files(rating_key, args)
    for f in files:
        print("fill trigger:", d.get("event"), "->", f)
        # fire and forget so the listener stays responsive
        subprocess.Popen([args.fill, f])
    return files


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""
        # always 200 quickly so Plex doesn't retry/queue
        self.send_response(200)
        self.end_headers()
        payload = parse_multipart_payload(body, self.headers.get("Content-Type", ""))
        if payload:
            try:
                handle_payload(payload, self.server.cm_args)
            except Exception as e:  # noqa: BLE001
                print("handler error:", e, file=sys.stderr)

    def do_GET(self):  # noqa: N802 - simple health check
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"cache-mover plex webhook listener ok\n")

    def log_message(self, *a):  # silence default access logging
        return


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--scheme", default="http")
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--listen-port", type=int, default=8595)
    ap.add_argument(
        "--fill",
        default="/usr/local/emhttp/plugins/cache-mover/scripts/cache_mover_fill")
    args = ap.parse_args(argv)

    srv = HTTPServer((args.listen, args.listen_port), Handler)
    srv.cm_args = args
    print("plex webhook listener on %s:%d (fill=%s)" % (
        args.listen, args.listen_port, args.fill))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
