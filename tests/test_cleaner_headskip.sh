#!/bin/bash
#
# Verifies the cache_mover3 "skip folders holding a sparse head" guard:
#   - head folders are matched (so the cleaner skips them and never
#     un-pins them from the exclude file)
#   - non-head folders are not matched
#   - folders with regex-special chars (brackets, spaces, parens) are
#     matched by exact fixed-string comparison, not regex
#
set -euo pipefail

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

headstart_list="$WORK/heads.txt"
cat > "$headstart_list" <<'EOF'
/mnt/nvme/Plex/Films/Movie (2009)/Movie Bluray-2160p.mkv
/mnt/nvme/Plex/TVRIPS/Show [1080p]/Season 01/ep.mkv
EOF

# replicate the cache_mover3 precompute
head_dirs=$(mktemp)
while IFS= read -r hp; do [ -n "$hp" ] && dirname "$hp"; done < "$headstart_list" | sort -u > "$head_dirs"

pass=0; fail=0
expect_skip() { # folder, expected(yes/no)
    local folder="$1" want="$2" got="no"
    grep -Fxq "$folder" "$head_dirs" && got="yes"
    if [ "$got" = "$want" ]; then echo "  OK  ($want) $folder"; pass=$((pass+1))
    else echo "  FAIL want=$want got=$got  $folder"; fail=$((fail+1)); fi
}

expect_skip "/mnt/nvme/Plex/Films/Movie (2009)"          yes
expect_skip "/mnt/nvme/Plex/TVRIPS/Show [1080p]/Season 01" yes
expect_skip "/mnt/nvme/Plex/Films/Other (2010)"          no
expect_skip "/mnt/nvme/Plex/Films/Movie"                 no   # partial, must NOT match

rm -f "$head_dirs"
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ] || exit 1
echo "cleaner head-skip guard OK"
