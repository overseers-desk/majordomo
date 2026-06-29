#!/usr/bin/env bash
# majordomo integration smoke — runs every command across both backends and
# checks each exits without error (exit 0). Read tests only; nothing is written
# to any space. See tests/INTEGRATION.md for the command-by-command rationale.
#
# A backend whose precondition is unmet (cache DB down, or live token invalid)
# has its whole matrix SKIPPED, not FAILED: a skip means "could not test", a
# fail means "the command errored when it should not have".
#
# Test fixtures (override with env vars):
#   MAJORDOMO_TEST_SPACE  a real group space the subject reads      (spaces/<id>)
#   MAJORDOMO_TEST_DM     a 1:1 DM the subject uses daily           (spaces/<id>)
# The web URL https://chat.google.com/app/chat/<id> carries <id>; the API
# resource name is spaces/<id>. The live preflight verifies both ids resolve.

set -u

MAJORDOMO="${MAJORDOMO:-majordomo}"
TEST_SPACE="${MAJORDOMO_TEST_SPACE:-spaces/AAQAGiUqUAU}"
TEST_DM="${MAJORDOMO_TEST_DM:-spaces/jP4cXEAAAAE}"
DM_FRESH_DAYS="${MAJORDOMO_DM_FRESH_DAYS:-1}"   # the subject DMs daily; staler than this is suspect

PASS=0 FAIL=0 SKIP=0 WARN=0
ERRF="$(mktemp)"
trap 'rm -f "$ERRF"' EXIT

pass() { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
skip() { SKIP=$((SKIP+1)); printf '  SKIP  %s\n' "$1"; }
warn() { WARN=$((WARN+1)); printf '  WARN  %s\n' "$1"; }
fail() {
  FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"
  sed 's/^/          | /' "$ERRF" | head -4
}

# run "<description>" <argv...>  — expect exit 0
run() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>"$ERRF"; then pass "$desc"; else fail "$desc  [$*]"; fi
}

# Probe a backend: does `<flag> spaces` succeed?  flag is "" (auto), --cache, --live.
probe() { $MAJORDOMO $1 spaces --json >/dev/null 2>"$ERRF"; }

# The full read matrix for one source flag ("" | --cache | --live).
matrix() {
  local SRC="$1" tag="$2"
  echo "── $tag ────────────────────────────────────────────"

  # spaces
  run "$tag spaces"                       $MAJORDOMO $SRC spaces
  run "$tag spaces --minimal-messages 0"  $MAJORDOMO $SRC spaces --minimal-messages 0
  run "$tag spaces --json"                $MAJORDOMO $SRC spaces --json
  run "$tag spaces --csv"                 $MAJORDOMO $SRC spaces --csv

  # people — sweep every window (exercises dates.resolve branches)
  local w
  for w in 7d 30d month year all; do
    run "$tag people --window $w"         $MAJORDOMO $SRC people --window "$w"
  done
  run "$tag people --since 2026-01-01 --until 2026-06-01" \
                                          $MAJORDOMO $SRC people --since 2026-01-01 --until 2026-06-01
  run "$tag people --json"                $MAJORDOMO $SRC people --json
  run "$tag people --csv"                 $MAJORDOMO $SRC people --csv

  # tasks — every filter
  run "$tag tasks"                        $MAJORDOMO $SRC tasks
  run "$tag tasks --to-me"                $MAJORDOMO $SRC tasks --to-me
  run "$tag tasks --by-me"                $MAJORDOMO $SRC tasks --by-me
  run "$tag tasks --assignee-name '*'"    $MAJORDOMO $SRC tasks --assignee-name '*'
  run "$tag tasks --space TEST_SPACE"     $MAJORDOMO $SRC tasks --space "$TEST_SPACE"
  run "$tag tasks --window all"           $MAJORDOMO $SRC tasks --window all
  run "$tag tasks --limit 5"              $MAJORDOMO $SRC tasks --limit 5
  run "$tag tasks --json"                 $MAJORDOMO $SRC tasks --json
  run "$tag tasks --csv"                  $MAJORDOMO $SRC tasks --csv

  # messages — group space and DM, plus a self-fed thread
  run "$tag messages --space TEST_SPACE"  $MAJORDOMO $SRC messages --space "$TEST_SPACE"
  run "$tag messages --space TEST_DM"     $MAJORDOMO $SRC messages --space "$TEST_DM"
  run "$tag messages --window all"        $MAJORDOMO $SRC messages --space "$TEST_SPACE" --window all
  run "$tag messages --json"              $MAJORDOMO $SRC messages --space "$TEST_SPACE" --json
  run "$tag messages --csv"               $MAJORDOMO $SRC messages --space "$TEST_SPACE" --csv

  # --thread: lift a real message name from the space, then dump its thread
  local mname
  mname=$($MAJORDOMO $SRC messages --space "$TEST_SPACE" --window all --json 2>/dev/null \
            | python3 -c 'import sys,json; r=json.load(sys.stdin)["rows"]; print(r[0]["name"]) if r else None' 2>/dev/null)
  if [ -n "${mname:-}" ] && [ "$mname" != "None" ]; then
    run "$tag messages --thread <derived>" $MAJORDOMO $SRC messages --thread "$mname"
  else
    skip "$tag messages --thread (no message in TEST_SPACE to derive a thread)"
  fi
}

echo "majordomo integration smoke — $($MAJORDOMO --help >/dev/null 2>&1 && echo "$MAJORDOMO found" || echo "$MAJORDOMO MISSING")"
command -v "$MAJORDOMO" >/dev/null 2>&1 || { echo "FATAL: $MAJORDOMO not on PATH"; exit 2; }

# ---- backend-independent commands -------------------------------------------
echo "── front-door / guards ──────────────────────────────────"
run  "install-claude-command"            $MAJORDOMO install-claude-command
# mutual-exclusion guard must reject (exit 2), so success here is a *failure*
if $MAJORDOMO --live --cache spaces >/dev/null 2>"$ERRF"; then
  fail "--live --cache should be rejected but exited 0"
else
  pass "--live --cache rejected (guard)"
fi
# login is interactive (opens a browser); cannot run unattended
skip "login (interactive browser flow — verify by hand; see INTEGRATION.md)"
# mcp boots a stdio server; smoke that it starts without crashing
if timeout 3 $MAJORDOMO mcp </dev/null >/dev/null 2>"$ERRF"; then
  pass "mcp boots (clean EOF exit)"
else
  rc=$?
  if [ "$rc" = 124 ]; then pass "mcp boots (ran until timeout)"
  elif grep -q "needs the extra" "$ERRF"; then skip "mcp (mcp extra not installed)"
  else fail "mcp failed to boot"; fi
fi

# ---- cache + auto + live matrices -------------------------------------------
if probe "--cache"; then matrix "--cache" "cache"; else skip "cache matrix (DB unreachable)"; fi
if probe "";        then matrix ""        "auto "; else skip "auto matrix (no backend reachable)"; fi
if probe "--live"; then
  # verify the configured fixtures actually exist before trusting the live matrix
  ids=$($MAJORDOMO --live spaces --json 2>/dev/null | python3 -c 'import sys,json;print(" ".join(r["space_name"] for r in json.load(sys.stdin)["rows"]))' 2>/dev/null)
  case " $ids " in *" $TEST_SPACE "*) :;; *) warn "TEST_SPACE $TEST_SPACE not in live space list — fixture id may be wrong";; esac
  case " $ids " in *" $TEST_DM "*)    :;; *) warn "TEST_DM $TEST_DM not in live space list — fixture id may be wrong";; esac

  matrix "--live" "live "

  # DM freshness: the subject DMs daily, so a stale newest message signals a broken pipeline
  newest=$($MAJORDOMO --live messages --space "$TEST_DM" --window 7d --json 2>/dev/null \
            | python3 -c 'import sys,json; r=json.load(sys.stdin)["rows"]; print(max((x.get("create_time") or "") for x in r)) if r else print("")' 2>/dev/null)
  if [ -z "$newest" ]; then
    warn "TEST_DM has no message in 7d — fixture stale or wrong id"
  else
    python3 - "$newest" "$DM_FRESH_DAYS" <<'PY' && pass "TEST_DM fresh (newest $newest)" || warn "TEST_DM stale (newest $newest) — daily DM expected"
import sys,datetime
ts=sys.argv[1].replace("Z","+00:00")
try: dt=datetime.datetime.fromisoformat(ts).replace(tzinfo=None)
except ValueError: sys.exit(1)
age=(datetime.datetime.utcnow()-dt).days
sys.exit(0 if age<=int(sys.argv[2]) else 1)
PY
  fi
else
  skip "live matrix (token invalid — run \`majordomo login\` first)"
fi

echo "──────────────────────────────────────────────────────────"
printf 'PASS %d  FAIL %d  SKIP %d  WARN %d\n' "$PASS" "$FAIL" "$SKIP" "$WARN"
[ "$FAIL" -eq 0 ]
