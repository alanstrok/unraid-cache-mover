#!/usr/bin/env python3
"""
Plex API helper for the cache-mover plugin.

Emits a single JSON document on stdout with four arrays:
  - active    : files from /status/sessions (never evict these)
  - episodes  : anchor episodes + next N unwatched after each anchor
  - movies    : currently-playing movies + recent on-deck movies
  - watched   : allLeaves entries with viewCount>=1 under subfolders filter

The bash driver (cache_mover_plex) consumes this and performs the actual
rsync/rm operations. Exit code 1 on any HTTP/parse failure so the driver
can skip the run without alarming.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional


class PlexClient:
    def __init__(self, scheme: str, host: str, port: str, token: str, timeout: float = 5.0):
        self.base = f"{scheme}://{host}:{port}"
        self.token = token
        self.timeout = timeout

    def get(self, path: str) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        sep = "&" if "?" in path else "?"
        url = f"{url}{sep}X-Plex-Token={urllib.parse.quote(self.token)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                    if not data:
                        return {}
                    return json.loads(data.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.5)
        raise RuntimeError(f"plex GET {path} failed: {last_err}")


class FixtureClient:
    def __init__(self, fixture_dir: str):
        self.fixture_dir = fixture_dir

    def get(self, path: str) -> Dict[str, Any]:
        # Map Plex API paths to fixture filenames.
        if path.startswith("/status/sessions"):
            name = "sessions.json"
        elif path.startswith("/library/onDeck"):
            name = "ondeck.json"
        elif path.startswith("/library/metadata/"):
            m = re.match(r"^/library/metadata/([^/]+)/allLeaves", path)
            if m:
                name = f"allLeaves_{m.group(1)}.json"
            else:
                m2 = re.match(r"^/library/metadata/([^/?]+)", path)
                name = f"metadata_{m2.group(1)}.json" if m2 else "metadata.json"
        else:
            name = path.strip("/").replace("/", "_") + ".json"
        full = os.path.join(self.fixture_dir, name)
        if not os.path.exists(full):
            return {"MediaContainer": {}}
        with open(full, "r", encoding="utf-8") as fh:
            return json.load(fh)


def _container(resp: Dict[str, Any]) -> Dict[str, Any]:
    return resp.get("MediaContainer", {}) if isinstance(resp, dict) else {}


def _metadata(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    container = _container(resp)
    md = container.get("Metadata") or container.get("Video") or []
    if isinstance(md, dict):
        return [md]
    return md or []


def _first_file(item: Dict[str, Any]) -> Optional[str]:
    media = item.get("Media") or []
    if isinstance(media, dict):
        media = [media]
    for m in media:
        parts = m.get("Part") or []
        if isinstance(parts, dict):
            parts = [parts]
        for p in parts:
            f = p.get("file")
            if f:
                return f
    return None


def _all_files(item: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    media = item.get("Media") or []
    if isinstance(media, dict):
        media = [media]
    for m in media:
        parts = m.get("Part") or []
        if isinstance(parts, dict):
            parts = [parts]
        for p in parts:
            f = p.get("file")
            if f:
                out.append(f)
    return out


def fetch_sessions(client) -> List[Dict[str, Any]]:
    resp = client.get("/status/sessions")
    results = []
    for item in _metadata(resp):
        t = item.get("type")
        if t not in ("episode", "movie"):
            continue
        for f in _all_files(item):
            results.append({
                "type": t,
                "file": f,
                "rating_key": item.get("ratingKey"),
                "grandparent_rating_key": item.get("grandparentRatingKey"),
                "parent_index": item.get("parentIndex"),
                "index": item.get("index"),
            })
    return results


def fetch_ondeck(client) -> List[Dict[str, Any]]:
    resp = client.get("/library/onDeck")
    results = []
    for item in _metadata(resp):
        t = item.get("type")
        if t not in ("episode", "movie"):
            continue
        for f in _all_files(item):
            results.append({
                "type": t,
                "file": f,
                "rating_key": item.get("ratingKey"),
                "grandparent_rating_key": item.get("grandparentRatingKey"),
                "parent_index": item.get("parentIndex"),
                "index": item.get("index"),
                "last_viewed_at": int(item.get("lastViewedAt") or 0),
            })
    return results


def fetch_all_leaves(client, show_rating_key: str) -> List[Dict[str, Any]]:
    resp = client.get(f"/library/metadata/{show_rating_key}/allLeaves")
    episodes = []
    for item in _metadata(resp):
        if item.get("type") != "episode":
            continue
        for f in _all_files(item):
            episodes.append({
                "file": f,
                "parent_index": int(item.get("parentIndex") or 0),
                "index": int(item.get("index") or 0),
                "view_count": int(item.get("viewCount") or 0),
                "last_viewed_at": int(item.get("lastViewedAt") or 0),
            })
    episodes.sort(key=lambda e: (e["parent_index"], e["index"]))
    return episodes


def _filter_regex(patterns: str) -> Optional[re.Pattern]:
    patterns = (patterns or "").strip()
    if not patterns:
        return None
    parts = [re.escape(p) for p in patterns.split() if p]
    if not parts:
        return None
    return re.compile("|".join(parts))


def _path_allowed(path: str, subfolders: Optional[re.Pattern],
                  filetypes: Optional[re.Pattern],
                  exclusions: Optional[re.Pattern]) -> bool:
    if subfolders and not subfolders.search(path):
        return False
    if filetypes and not filetypes.search(path):
        return False
    if exclusions and exclusions.search(path):
        return False
    return True


def compute_targets(client, cfg) -> Dict[str, List[Dict[str, Any]]]:
    legacy_sub_re = _filter_regex(cfg.subfolders)
    tv_sub_re = _filter_regex(cfg.tv_subfolders) or legacy_sub_re
    movie_sub_re = _filter_regex(cfg.movie_subfolders) or legacy_sub_re
    ft_re = _filter_regex(cfg.filetypes)
    ex_re = _filter_regex(cfg.exclusions)

    def _tv_allowed(path: str) -> bool:
        return _path_allowed(path, tv_sub_re, ft_re, ex_re)

    def _movie_allowed(path: str) -> bool:
        return _path_allowed(path, movie_sub_re, ft_re, ex_re)

    sessions = fetch_sessions(client)
    ondeck = fetch_ondeck(client)

    now = int(time.time())
    tv_max_age_sec = int(cfg.tv_ondeck_max_age_days) * 86400
    movie_max_age_sec = int(cfg.movie_ondeck_max_age_days) * 86400

    active: List[Dict[str, Any]] = []
    episode_anchors: List[Dict[str, Any]] = []
    movies: List[Dict[str, Any]] = []

    for s in sessions:
        if s["type"] == "episode":
            if not _tv_allowed(s["file"]):
                continue
            active.append({"plex_path": s["file"]})
            episode_anchors.append(s)
        elif s["type"] == "movie":
            if not _movie_allowed(s["file"]):
                continue
            active.append({"plex_path": s["file"]})
            if cfg.include_movies:
                movies.append({"plex_path": s["file"]})

    for d in ondeck:
        if d["type"] == "episode":
            if d["last_viewed_at"] and (now - d["last_viewed_at"]) > tv_max_age_sec:
                continue
            if not _tv_allowed(d["file"]):
                continue
            episode_anchors.append(d)
        elif d["type"] == "movie" and cfg.include_movies:
            if d["last_viewed_at"] and (now - d["last_viewed_at"]) > movie_max_age_sec:
                continue
            if not _movie_allowed(d["file"]):
                continue
            movies.append({"plex_path": d["file"]})

    # Walk each unique show's allLeaves once. Include on-deck show
    # rating keys only if the on-deck entry is within the TV age window
    # — stale on-deck items should NOT trigger pre-caching.
    show_keys = sorted(
        {a["grandparent_rating_key"] for a in episode_anchors
         if a.get("grandparent_rating_key")}
        | {d["grandparent_rating_key"] for d in ondeck
           if d["type"] == "episode" and d.get("grandparent_rating_key")
           and (not d.get("last_viewed_at")
                or (now - d["last_viewed_at"]) <= tv_max_age_sec)}
    )
    leaves_by_show: Dict[str, List[Dict[str, Any]]] = {}
    for k in show_keys:
        try:
            leaves_by_show[k] = fetch_all_leaves(client, k)
        except Exception as exc:  # noqa: BLE001
            print(f"plex allLeaves {k} failed: {exc}", file=sys.stderr)
            leaves_by_show[k] = []

    episodes: List[Dict[str, Any]] = []
    seen_files: set = set()

    def _add_episode(entry: Dict[str, Any]) -> None:
        p = entry["file"]
        if p in seen_files:
            return
        if not _tv_allowed(p):
            return
        seen_files.add(p)
        episodes.append({
            "plex_path": p,
            "season": entry.get("parent_index"),
            "index": entry.get("index"),
        })

    def _walk_next(leaves: List[Dict[str, Any]], start_idx: int, n: int) -> None:
        taken = 0
        i = start_idx
        while i < len(leaves) and taken < n:
            ep = leaves[i]
            if not cfg.include_specials and ep["parent_index"] == 0:
                i += 1
                continue
            if ep["view_count"] == 0:
                _add_episode(ep)
                taken += 1
            i += 1

    for anchor in episode_anchors:
        show_key = anchor.get("grandparent_rating_key")
        leaves = leaves_by_show.get(show_key, [])
        if not leaves:
            # We still have the anchor's file path directly.
            _add_episode({"file": anchor["file"],
                          "parent_index": anchor.get("parent_index"),
                          "index": anchor.get("index")})
            continue
        anchor_idx = None
        for i, ep in enumerate(leaves):
            if ep["file"] == anchor["file"]:
                anchor_idx = i
                break
        if anchor_idx is None:
            # Fall back: locate by (season, index).
            for i, ep in enumerate(leaves):
                if (ep["parent_index"] == anchor.get("parent_index")
                        and ep["index"] == anchor.get("index")):
                    anchor_idx = i
                    break
        if anchor_idx is None:
            _add_episode({"file": anchor["file"],
                          "parent_index": anchor.get("parent_index"),
                          "index": anchor.get("index")})
            continue
        # Anchor itself: always include it (it's what the user is watching
        # or has on-deck).
        _add_episode(leaves[anchor_idx])
        _walk_next(leaves, anchor_idx + 1, int(cfg.precache_episodes))

    # Progression anchor: most-recently-watched episode per show even if
    # nothing is currently playing. Cache "next one + X" = 1 + N total,
    # matching the active/on-deck branch. Skip shows whose most-recently-
    # watched episode is older than the TV on-deck age window.
    for show_key, leaves in leaves_by_show.items():
        watched = [ep for ep in leaves if ep["view_count"] >= 1
                   and ep["last_viewed_at"] > 0]
        if not watched:
            continue
        watched.sort(key=lambda e: e["last_viewed_at"])
        last = watched[-1]
        if (now - last["last_viewed_at"]) > tv_max_age_sec:
            continue
        for i, ep in enumerate(leaves):
            if ep["file"] == last["file"]:
                _walk_next(leaves, i + 1, int(cfg.precache_episodes) + 1)
                break

    # Watched eviction candidates: every allLeaves episode with viewCount>=1
    # that passes the TV subfolders filter.
    watched_out: List[Dict[str, Any]] = []
    watched_seen: set = set()
    for leaves in leaves_by_show.values():
        for ep in leaves:
            if ep["view_count"] < 1:
                continue
            p = ep["file"]
            if p in watched_seen:
                continue
            if not _tv_allowed(p):
                continue
            watched_seen.add(p)
            watched_out.append({"plex_path": p})

    # Dedupe movies by path.
    movie_out: List[Dict[str, Any]] = []
    movie_seen: set = set()
    for m in movies:
        if m["plex_path"] in movie_seen:
            continue
        movie_seen.add(m["plex_path"])
        movie_out.append(m)

    return {
        "active": active,
        "episodes": episodes,
        "movies": movie_out,
        "watched": watched_out,
    }


def parse_server_spec(spec: str, default_scheme: str,
                      default_port: str) -> Optional[tuple]:
    """Parse one extra-server spec into (scheme, host, port, token).

    Accepted pipe-delimited forms (since every server shares the same
    library/paths, only the endpoint + token differ):
        host|token                 -> port/scheme inherited from primary
        host|port|token            -> scheme inherited from primary
        host|port|token|scheme     -> fully specified
    Empty port/scheme fields also inherit from the primary. Returns None
    for an unusable spec (missing host or token).
    """
    parts = [p.strip() for p in (spec or "").split("|")]
    if len(parts) == 2:
        host, token = parts
        port, scheme = "", ""
    elif len(parts) == 3:
        host, port, token = parts
        scheme = ""
    elif len(parts) >= 4:
        host, port, token, scheme = parts[0], parts[1], parts[2], parts[3]
    else:
        return None
    port = port or default_port
    scheme = scheme or default_scheme
    if not host or not token:
        return None
    return scheme, host, port, token


def merge_targets(results: List[Dict[str, List[Dict[str, Any]]]]
                  ) -> Dict[str, List[Dict[str, Any]]]:
    """Merge per-server target sets into a single decision set.

    Keep-sets (active/episodes/movies) are unioned across servers: if ANY
    server wants a file kept or pre-cached, we keep it. The evict-set
    (watched) is unioned too, then has the entire keep-set subtracted, so a
    file that one user finished but another user is still watching (or has
    queued ahead) is never evicted. Dedupe is by plex_path.
    """
    def _union(key: str):
        seen: set = set()
        out: List[Dict[str, Any]] = []
        for r in results:
            for e in r.get(key, []):
                p = e.get("plex_path")
                if not p or p in seen:
                    continue
                seen.add(p)
                out.append(e)
        return out, seen

    active, active_set = _union("active")
    episodes, episodes_set = _union("episodes")
    movies, movies_set = _union("movies")

    keep = active_set | episodes_set | movies_set

    watched: List[Dict[str, Any]] = []
    watched_seen: set = set()
    for r in results:
        for w in r.get("watched", []):
            p = w.get("plex_path")
            if not p or p in keep or p in watched_seen:
                continue
            watched_seen.add(p)
            watched.append(w)

    return {
        "active": active,
        "episodes": episodes,
        "movies": movies,
        "watched": watched,
    }


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["targets"])
    ap.add_argument("--host", default="")
    ap.add_argument("--port", default="32400")
    ap.add_argument("--scheme", default="http")
    ap.add_argument("--token", default="")
    ap.add_argument("--precache-episodes", dest="precache_episodes", default="2")
    ap.add_argument("--ondeck-max-age-days", dest="ondeck_max_age_days", default="7")
    ap.add_argument("--tv-ondeck-max-age-days", dest="tv_ondeck_max_age_days", default="")
    ap.add_argument("--movie-ondeck-max-age-days", dest="movie_ondeck_max_age_days", default="")
    ap.add_argument("--include-movies", dest="include_movies", default="yes")
    ap.add_argument("--include-specials", dest="include_specials", default="no")
    ap.add_argument("--subfolders", default="")
    ap.add_argument("--tv-subfolders", dest="tv_subfolders", default="")
    ap.add_argument("--movie-subfolders", dest="movie_subfolders", default="")
    ap.add_argument("--filetypes", default="")
    ap.add_argument("--exclusions", default="")
    ap.add_argument("--fixture", default="")
    # Additional Plex servers sharing the same library/paths. Repeatable.
    # Each value is a pipe-delimited spec (see parse_server_spec).
    ap.add_argument("--extra-server", dest="extra_server", action="append",
                    default=[])
    # Additional fixture directories (offline tests only). Repeatable.
    ap.add_argument("--extra-fixture", dest="extra_fixture", action="append",
                    default=[])
    args = ap.parse_args(argv)

    args.include_movies = args.include_movies.lower() == "yes"
    args.include_specials = args.include_specials.lower() == "yes"

    # Legacy fallback: if TV/movie-specific flags are empty, fall back to
    # the single --ondeck-max-age-days / --subfolders values.
    if not args.tv_ondeck_max_age_days:
        args.tv_ondeck_max_age_days = args.ondeck_max_age_days
    if not args.movie_ondeck_max_age_days:
        args.movie_ondeck_max_age_days = args.ondeck_max_age_days

    # Build the client list: primary first, then any additional servers.
    # All servers share the same library/paths, so the same cfg/filters
    # apply to every one of them.
    clients: List[tuple] = []
    any_failed = False

    if args.fixture:
        clients.append(("fixture", FixtureClient(args.fixture)))
    elif args.host and args.token:
        clients.append((args.host,
                        PlexClient(args.scheme, args.host, args.port, args.token)))

    for fx in (args.extra_fixture or []):
        if not fx:
            continue
        label = os.path.basename(fx.rstrip("/")) or fx
        clients.append((f"fixture:{label}", FixtureClient(fx)))

    for spec in (args.extra_server or []):
        if not (spec or "").strip():
            continue
        parsed = parse_server_spec(spec, args.scheme, args.port)
        if not parsed:
            print(f"plex_api: ignoring malformed extra server spec: {spec!r}",
                  file=sys.stderr)
            any_failed = True
            continue
        scheme, host, port, token = parsed
        clients.append((host, PlexClient(scheme, host, port, token)))

    if not clients:
        print("missing host/token", file=sys.stderr)
        return 1

    results: List[Dict[str, List[Dict[str, Any]]]] = []
    for label, client in clients:
        try:
            results.append(compute_targets(client, args))
        except Exception as exc:  # noqa: BLE001
            any_failed = True
            print(f"plex_api: server {label} failed: {exc}", file=sys.stderr)

    if not results:
        print("plex_api: all servers failed", file=sys.stderr)
        return 1

    merged = merge_targets(results)

    # Safety: if any configured server was unreachable or malformed, our view
    # of what's wanted is incomplete. Pre-caching from the servers we reached
    # is harmless, but eviction is not -- a file another server still wants
    # could be deleted. Suppress eviction for this run.
    if any_failed:
        merged["watched"] = []
        print("plex_api: one or more servers failed; skipping eviction this run",
              file=sys.stderr)

    json.dump(merged, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
