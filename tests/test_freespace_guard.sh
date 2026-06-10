#!/bin/bash
#
# Verifies the head-fill free-space guard decision:
#   need  = fsize - already_allocated
#   skip  when (avail - need) < min_free_space_gb
#
set -euo pipefail
GB=$((1024*1024*1024))

# mirrors the guard in cache_mover_fill / fill_head
should_fill() { # avail_bytes need_bytes min_gb  -> echoes fill|skip
    local avail="$1" need="$2" min_gb="$3"
    local min_bytes=$(( min_gb * GB ))
    if [ "$need" -gt 0 ] && [ $((avail - need)) -lt "$min_bytes" ]; then echo skip; else echo fill; fi
}

pass=0; fail=0
check() { # name want avail need min
    local got; got=$(should_fill "$3" "$4" "$5")
    if [ "$got" = "$2" ]; then echo "  OK  $1 ($got)"; pass=$((pass+1))
    else echo "  FAIL $1 want=$2 got=$got"; fail=$((fail+1)); fi
}

#       name                       want  avail        need        min
check "plenty of space"           fill  $((500*GB))  $((3*GB))   10
check "exactly at floor -> fill"  fill  $((13*GB))   $((3*GB))   10   # 13-3=10, not < 10
check "one byte under -> skip"    skip  $((13*GB-1)) $((3*GB))   10
check "well under floor -> skip"  skip  $((11*GB))   $((5*GB))   10
check "already filled (need<=0)"  fill  $((1*GB))    0           10   # nothing to write

echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ] || exit 1
echo "free-space guard OK"
