#!/bin/bash
#
# Offline tests for the "head start" core mechanics.
#
# These do not need a real Unraid array - they exercise the two pieces
# the feature depends on:
#   1. Sparse head creation: a stub that is `headstart_mb` of real data
#      truncated to the full original size (apparent size == original,
#      allocated blocks << original).
#   2. In-place fill: rsync --inplace turns the stub into a full copy
#      WITHOUT changing the inode, so a player that already opened the
#      stub keeps reading the same file as the tail fills in.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
check() {
    local name="$1" cond="$2"
    if [ "$cond" = "1" ]; then
        echo "  OK  $name"; pass=$((pass + 1))
    else
        echo "  FAIL $name"; fail=$((fail + 1))
    fi
}

allocated_bytes() { echo $(( $(stat -c %b "$1") * 512 )); }
apparent_bytes()  { stat -c %s "$1"; }
inode_of()        { stat -c %i "$1"; }

HEAD_MB=2
HEAD_BYTES=$((HEAD_MB * 1024 * 1024))

# A 16 MB "media file" full of random data (so checksums are meaningful).
SRC="$WORK/array/Show/file.mkv"
mkdir -p "$(dirname "$SRC")"
dd if=/dev/urandom of="$SRC" bs=1M count=16 status=none
SRC_SIZE=$(apparent_bytes "$SRC")

########################################################################
echo "=== Head creation (sparse stub) ==="
DST="$WORK/pool/Show/file.mkv"
mkdir -p "$(dirname "$DST")"
head -c "$HEAD_BYTES" "$SRC" > "$DST"
truncate -s "$SRC_SIZE" "$DST"

[ "$(apparent_bytes "$DST")" -eq "$SRC_SIZE" ] && check "head apparent size == source" 1 || check "head apparent size == source" 0
[ "$(allocated_bytes "$DST")" -lt "$SRC_SIZE" ] && check "head is sparse (allocated < source)" 1 || check "head is sparse (allocated < source)" 0
# the real bytes at the front must equal the source head
if cmp -s <(head -c "$HEAD_BYTES" "$SRC") <(head -c "$HEAD_BYTES" "$DST"); then
    check "head bytes match source head" 1
else
    check "head bytes match source head" 0
fi

HEAD_INODE=$(inode_of "$DST")

########################################################################
echo "=== In-place fill preserves inode and content ==="
# The head carries the source's mtime and the same apparent size, so a
# plain rsync would skip it on the size+mtime quick check. Match the
# seeder: stamp the source mtime onto the stub, then prove the fill still
# works (it must use --ignore-times).
touch -m -r "$SRC" "$DST"
# Open the stub and hold the fd, mimicking a player that started on it.
exec 9<"$DST"
rsync -a --inplace --ignore-times "$SRC" "$DST"

[ "$(inode_of "$DST")" -eq "$HEAD_INODE" ] && check "inode unchanged after fill" 1 || check "inode unchanged after fill" 0
[ "$(apparent_bytes "$DST")" -eq "$SRC_SIZE" ] && check "filled apparent size == source" 1 || check "filled apparent size == source" 0
[ "$(allocated_bytes "$DST")" -ge $((SRC_SIZE * 95 / 100)) ] && check "filled is fully allocated" 1 || check "filled is fully allocated" 0
if cmp -s "$SRC" "$DST"; then
    check "filled content == source" 1
else
    check "filled content == source" 0
fi
exec 9<&-

########################################################################
echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ] || exit 1
echo "All head-start mechanic tests passed."
