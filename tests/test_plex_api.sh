#!/bin/bash
#
# Offline test for plex_api.py. Builds a fixture dir with time-sensitive
# onDeck timestamps and runs the script against it, asserting the JSON
# output matches expectations.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO/src/usr/local/emhttp/plugins/cache-mover/scripts/plex_api.py"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Copy static fixtures into the workdir.
cp "$HERE/fixtures/plex/sessions.json" "$WORK/sessions.json"
cp "$HERE/fixtures/plex/allLeaves_1001.json" "$WORK/allLeaves_1001.json"

# Build onDeck fixture with current-time-relative timestamps.
NOW=$(date +%s)
RECENT=$((NOW - 2 * 86400))   # 2 days ago: within 7-day window
OLD=$((NOW - 60 * 86400))     # 60 days ago: outside 7-day window

cat > "$WORK/ondeck.json" <<EOF
{
  "MediaContainer": {
    "size": 2,
    "Metadata": [
      {
        "type": "movie",
        "ratingKey": "9001",
        "lastViewedAt": $RECENT,
        "Media": [{"Part": [{"file": "/data/Movies/RecentMovie (2024)/RecentMovie.mkv"}]}]
      },
      {
        "type": "movie",
        "ratingKey": "9002",
        "lastViewedAt": $OLD,
        "Media": [{"Part": [{"file": "/data/Movies/StaleMovie (2015)/StaleMovie.mkv"}]}]
      }
    ]
  }
}
EOF

OUT=$(python3 "$SCRIPT" targets --fixture "$WORK" \
    --precache-episodes 2 --ondeck-max-age-days 7 \
    --include-movies yes --include-specials no \
    --subfolders "TVRIPS Movies")

echo "--- plex_api.py output ---"
echo "$OUT" | python3 -m json.tool
echo "--------------------------"

check() {
    local name="$1" expect="$2" actual="$3"
    if [ "$expect" = "$actual" ]; then
        echo "OK  $name"
    else
        echo "FAIL $name: expected=$expect actual=$actual"
        exit 1
    fi
}

EPISODES=$(echo "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("\n".join(sorted(e["plex_path"] for e in d["episodes"])))')
MOVIES=$(echo "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("\n".join(sorted(m["plex_path"] for m in d["movies"])))')
WATCHED=$(echo "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("\n".join(sorted(w["plex_path"] for w in d["watched"])))')
ACTIVE=$(echo "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("\n".join(sorted(a["plex_path"] for a in d["active"])))')

EXPECT_EPISODES="/data/TVRIPS/TestShow/Season 02/TestShow - S02E05.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E06.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E07.mkv"

EXPECT_MOVIES="/data/Movies/RecentMovie (2024)/RecentMovie.mkv"

EXPECT_WATCHED="/data/TVRIPS/TestShow/Season 02/TestShow - S02E01.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E02.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E03.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E04.mkv"

EXPECT_ACTIVE="/data/TVRIPS/TestShow/Season 02/TestShow - S02E05.mkv"

check "episodes"   "$EXPECT_EPISODES" "$EPISODES"
check "movies"     "$EXPECT_MOVIES"   "$MOVIES"
check "watched"    "$EXPECT_WATCHED"  "$WATCHED"
check "active"     "$EXPECT_ACTIVE"   "$ACTIVE"

# Specials excluded by default:
if echo "$EPISODES" | grep -q "S00E01"; then
    echo "FAIL specials excluded: S00E01 should not appear"
    exit 1
fi
echo "OK  specials excluded"

# Stale on-deck movie excluded:
if echo "$MOVIES" | grep -q "StaleMovie"; then
    echo "FAIL stale on-deck movie excluded: StaleMovie should not appear"
    exit 1
fi
echo "OK  stale on-deck movie excluded"

echo
echo "All plex_api.py fixture tests passed."
