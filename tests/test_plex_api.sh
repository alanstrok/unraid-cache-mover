#!/bin/bash
#
# Offline tests for plex_api.py.
#
# Run A: active playback + TV subfolder filter.
# Run B: progression-only (nothing playing, stale on-deck, last-watched anchor).
# Run C: X=0 edge case for both active and progression.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO/src/usr/local/emhttp/plugins/cache-mover/scripts/plex_api.py"

WORK_ACTIVE=$(mktemp -d)
WORK_PROG="$HERE/fixtures/plex-progression"
trap 'rm -rf "$WORK_ACTIVE" "$WORK_PROG_RECENT" 2>/dev/null' EXIT

# Stage the active-playback fixtures into a tmp dir so we can drop a
# fresh ondeck.json with current-time-relative timestamps.
cp "$HERE/fixtures/plex/sessions.json" "$WORK_ACTIVE/sessions.json"
cp "$HERE/fixtures/plex/allLeaves_1001.json" "$WORK_ACTIVE/allLeaves_1001.json"

NOW=$(date +%s)
RECENT=$((NOW - 2 * 86400))   # 2 days ago: within 7-day movie window
OLD=$((NOW - 60 * 86400))     # 60 days ago: outside 7-day movie window

cat > "$WORK_ACTIVE/ondeck.json" <<EOF
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

pass=0
fail=0
check() {
    local name="$1" expect="$2" actual="$3"
    if [ "$expect" = "$actual" ]; then
        echo "  OK  $name"
        pass=$((pass + 1))
    else
        echo "  FAIL $name"
        echo "      expected: $(echo "$expect" | tr '\n' '|')"
        echo "      actual:   $(echo "$actual" | tr '\n' '|')"
        fail=$((fail + 1))
    fi
}

extract() {
    # extract sorted plex_paths from one of the result arrays
    local key="$1"
    python3 -c "
import sys, json
d = json.load(sys.stdin)
print('\n'.join(sorted(e['plex_path'] for e in d['$key'])))
"
}

run_targets() {
    local fixture="$1"; shift
    python3 "$SCRIPT" targets --fixture "$fixture" \
        --tv-subfolders "TVRIPS" \
        --movie-subfolders "Movies" \
        --tv-ondeck-max-age-days 14 \
        --movie-ondeck-max-age-days 7 \
        --include-movies yes --include-specials no \
        "$@"
}

########################################################################
echo "=== Run A: active playback (S02E05 in sessions, X=2) ==="
OUT_A=$(run_targets "$WORK_ACTIVE" --precache-episodes 2)

EXPECT_A_EPISODES="/data/TVRIPS/TestShow/Season 02/TestShow - S02E05.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E06.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E07.mkv"
EXPECT_A_MOVIES="/data/Movies/RecentMovie (2024)/RecentMovie.mkv"
EXPECT_A_WATCHED="/data/TVRIPS/TestShow/Season 02/TestShow - S02E01.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E02.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E03.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E04.mkv"
EXPECT_A_ACTIVE="/data/TVRIPS/TestShow/Season 02/TestShow - S02E05.mkv"

check "A.episodes (current + 2 next)" "$EXPECT_A_EPISODES" "$(echo "$OUT_A" | extract episodes)"
check "A.movies   (stale filtered)"   "$EXPECT_A_MOVIES"   "$(echo "$OUT_A" | extract movies)"
check "A.watched  (S02E01-E04)"       "$EXPECT_A_WATCHED"  "$(echo "$OUT_A" | extract watched)"
check "A.active   (S02E05 only)"      "$EXPECT_A_ACTIVE"   "$(echo "$OUT_A" | extract active)"

# TV regex excludes specials path (S00E01 is in Specials/, not TVRIPS/... wait, it is
# under TVRIPS/TestShow/Specials, which matches "TVRIPS" regex). The specials filter
# is governed by include-specials=no, not the subfolder regex.
if echo "$OUT_A" | extract episodes | grep -q "S00E01"; then
    echo "  FAIL A.specials excluded"; fail=$((fail + 1))
else
    echo "  OK  A.specials excluded"; pass=$((pass + 1))
fi

########################################################################
echo "=== Run B: stale on-deck, nothing playing — should NOT pre-cache ==="
OUT_B=$(run_targets "$WORK_PROG" --precache-episodes 2)

# On-deck entry is from 2020 (way outside 14-day window). Both the
# on-deck filter AND the progression branch should skip this show.
EXPECT_B_EPISODES=""
EXPECT_B_ACTIVE=""
EXPECT_B_MOVIES=""

check "B.episodes (stale = empty)"     "$EXPECT_B_EPISODES" "$(echo "$OUT_B" | extract episodes)"
check "B.active   (empty)"             "$EXPECT_B_ACTIVE"   "$(echo "$OUT_B" | extract active)"
check "B.movies   (empty)"             "$EXPECT_B_MOVIES"   "$(echo "$OUT_B" | extract movies)"

########################################################################
echo "=== Run B2: recent on-deck, nothing playing — progression SHOULD work ==="
# Build a progression fixture with a RECENT on-deck lastViewedAt.
WORK_PROG_RECENT=$(mktemp -d)
cp "$HERE/fixtures/plex-progression/sessions.json" "$WORK_PROG_RECENT/sessions.json"
cp "$HERE/fixtures/plex/allLeaves_1001.json" "$WORK_PROG_RECENT/allLeaves_1001.json"
RECENT_PROG=$((NOW - 3 * 86400))  # 3 days ago: within 14-day window
cat > "$WORK_PROG_RECENT/ondeck.json" <<EOF
{
  "MediaContainer": {
    "size": 1,
    "Metadata": [
      {
        "type": "episode",
        "ratingKey": "5006",
        "grandparentRatingKey": "1001",
        "parentIndex": 2,
        "index": 6,
        "lastViewedAt": $RECENT_PROG,
        "Media": [{"Part": [{"file": "/data/TVRIPS/TestShow/Season 02/TestShow - S02E06.mkv"}]}]
      }
    ]
  }
}
EOF

OUT_B2=$(run_targets "$WORK_PROG_RECENT" --precache-episodes 2)

# On-deck E06 within 14 days → becomes anchor → E06 + 2 next unwatched
# (E07, E10). Progression branch skips because allLeaves' most-recently-
# watched (E04) is from 2023 — older than 14-day window.
EXPECT_B2_EPISODES="/data/TVRIPS/TestShow/Season 02/TestShow - S02E06.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E07.mkv
/data/TVRIPS/TestShow/Season 02/TestShow - S02E10.mkv"

check "B2.episodes (recent progression)" "$EXPECT_B2_EPISODES" "$(echo "$OUT_B2" | extract episodes)"
check "B2.active   (empty)"              ""                     "$(echo "$OUT_B2" | extract active)"

rm -rf "$WORK_PROG_RECENT"

########################################################################
echo "=== Run C: X=0 edge cases ==="
OUT_C_ACTIVE=$(run_targets "$WORK_ACTIVE" --precache-episodes 0)

EXPECT_C_ACTIVE="/data/TVRIPS/TestShow/Season 02/TestShow - S02E05.mkv"

check "C.active   (anchor only)"        "$EXPECT_C_ACTIVE" "$(echo "$OUT_C_ACTIVE" | extract episodes)"
# Stale progression fixture: X=0 doesn't matter, show is too old → empty.
check "C.prog     (stale = empty)"      ""                  "$(run_targets "$WORK_PROG" --precache-episodes 0 | extract episodes)"

########################################################################
echo "=== Run D: TV/movie subfolder scoping (TV regex empty -> block TV) ==="
# Override tv_subfolders to a garbage name so the TV regex doesn't match
# any path. Movies should still come through because movie_subfolders
# still matches.
OUT_D=$(python3 "$SCRIPT" targets --fixture "$WORK_ACTIVE" \
    --tv-subfolders "NONEXISTENT" \
    --movie-subfolders "Movies" \
    --tv-ondeck-max-age-days 14 --movie-ondeck-max-age-days 7 \
    --include-movies yes --include-specials no \
    --precache-episodes 2)

check "D.episodes (TV filtered out)" "" "$(echo "$OUT_D" | extract episodes)"
check "D.movies   (still present)"   "/data/Movies/RecentMovie (2024)/RecentMovie.mkv" "$(echo "$OUT_D" | extract movies)"
check "D.watched  (TV filtered out)" "" "$(echo "$OUT_D" | extract watched)"

########################################################################
echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ] || exit 1
echo "All plex_api.py fixture tests passed."
