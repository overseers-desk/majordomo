#!/usr/bin/env bash
# majordomo DEPLOYMENT-READINESS gate.
#
# Not a software-strength test. The unit suite already proves the code logic
# against a fake Chat service. This script answers one question: is THIS machine,
# with THIS configuration and THESE credentials, ready to serve? It is the last
# stage before deployment, so anything that would stop a real user is a hard FAIL,
# never hidden: invalid OAuth client / dead token, cache DB server down, an MCP
# front door that will not start, missing or malformed config, a test space/DM
# that does not exist, a stale daily DM. Every readiness check is PASS or FAIL.
#
# One thing is NOT a readiness verdict: the harness losing its own network, or
# Google answering 429 ("retry later"). Either means the gate cannot complete, so
# it ABORTS with exit 2 ("cannot run, retry") rather than emitting false failures.
# A command that fails while the network is up and quota is intact is a real FAIL;
# a DB server that is down but whose host resolves stays a FAIL (a deployment
# problem, not a harness one).
#
# Exit: 0 ready · 1 not ready (FAIL>0) · 2 cannot run (network/quota).
# Read-only: no command writes to any space.
#
# Fixtures (override with env vars):
#   MAJORDOMO_TEST_SPACE  a real group space the subject reads   (spaces/<id>)
#   MAJORDOMO_TEST_DM     a 1:1 DM the subject uses daily        (spaces/<id>)
# Web URL https://chat.google.com/app/chat/<id> carries <id>; API name is spaces/<id>.

set -u

MAJORDOMO="${MAJORDOMO:-majordomo}"
CFGDIR="${MAJORDOMO_CONFIG_DIR:-$HOME/.config/majordomo}"
TEST_SPACE="${MAJORDOMO_TEST_SPACE:-spaces/AAQAGiUqUAU}"
TEST_DM="${MAJORDOMO_TEST_DM:-spaces/jP4cXEAAAAE}"
DM_FRESH_DAYS="${MAJORDOMO_DM_FRESH_DAYS:-1}"   # the subject DMs daily; staler than this is a broken pipeline

DBHOST=$(grep -E '^MYSQL_HOST' "$CFGDIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' "'"'")

PASS=0 FAIL=0
ERRF="$(mktemp)"
trap 'rm -f "$ERRF"' EXIT

pass() { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
fail() {
  FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"
  [ -s "$ERRF" ] && sed 's/^/          | /' "$ERRF" | head -4
}

# Can the harness resolve the names it must reach? Two tries to ride a 1-packet blip.
infra_up() {
  local h ok
  for h in oauth2.googleapis.com chat.googleapis.com ${DBHOST:+$DBHOST}; do
    ok=0
    getent hosts "$h" >/dev/null 2>&1 && ok=1 || { sleep 1; getent hosts "$h" >/dev/null 2>&1 && ok=1; }
    [ "$ok" = 1 ] || return 1
  done
  return 0
}

abort_retry() {
  printf '  RETRY %s\n' "$1"
  echo "──────────────────────────────────────────────────────────"
  echo "GATE ABORTED (exit 2): $2"
  echo "This is NOT a deployment verdict. Retry later."
  exit 2
}

# run "<desc>" <argv...>: PASS on exit 0. On non-zero, classify before judging:
#   API read quota (429, "retry later")  -> retry-abort, not a readiness FAIL
#   the harness network just dropped       -> retry-abort
#   anything else (network up)            -> real FAIL
run() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>"$ERRF"; then pass "$desc"; return; fi
  grep -qiE '429|exceeded the API quota|Retry this request later' "$ERRF" \
    && abort_retry "$desc" "Google Chat read quota exhausted. Rerun when it resets, or raise the project quota."
  infra_up || abort_retry "$desc" "the test machine cannot reach DNS/network."
  fail "$desc  [$*]"
}

# check "<desc>" <argv...>: local predicate (no network), PASS iff exit 0.
check() {
  local desc="$1"; shift
  if "$@" >"$ERRF" 2>&1; then pass "$desc"; else fail "$desc"; fi
}

# Full matrix for the local/cheap backends (cache, auto). No quota cost.
matrix() {
  local SRC="$1" tag="$2"
  echo "── $tag backend ─────────────────────────────────────"
  run "$tag spaces"                       $MAJORDOMO $SRC spaces
  run "$tag spaces --minimal-messages 0"  $MAJORDOMO $SRC spaces --minimal-messages 0
  run "$tag spaces --json"                $MAJORDOMO $SRC spaces --json
  run "$tag spaces --csv"                 $MAJORDOMO $SRC spaces --csv
  local w
  for w in 7d 30d month year all; do
    run "$tag people --window $w"         $MAJORDOMO $SRC people --window "$w"
  done
  run "$tag people --since/--until"       $MAJORDOMO $SRC people --since 2026-01-01 --until 2026-06-01
  run "$tag people --json"                $MAJORDOMO $SRC people --json
  run "$tag people --csv"                 $MAJORDOMO $SRC people --csv
  run "$tag tasks"                        $MAJORDOMO $SRC tasks
  run "$tag tasks --to-me"                $MAJORDOMO $SRC tasks --to-me
  run "$tag tasks --by-me"                $MAJORDOMO $SRC tasks --by-me
  run "$tag tasks --assignee-name '*'"    $MAJORDOMO $SRC tasks --assignee-name '*'
  run "$tag tasks --space TEST_SPACE"     $MAJORDOMO $SRC tasks --space "$TEST_SPACE"
  run "$tag tasks --window all"           $MAJORDOMO $SRC tasks --window all
  run "$tag tasks --limit 5"              $MAJORDOMO $SRC tasks --limit 5
  run "$tag tasks --json"                 $MAJORDOMO $SRC tasks --json
  run "$tag tasks --csv"                  $MAJORDOMO $SRC tasks --csv
  run "$tag messages --space TEST_SPACE"  $MAJORDOMO $SRC messages --space "$TEST_SPACE"
  run "$tag messages --space TEST_DM"     $MAJORDOMO $SRC messages --space "$TEST_DM"
  run "$tag messages --window all"        $MAJORDOMO $SRC messages --space "$TEST_SPACE" --window all
  run "$tag messages --json"              $MAJORDOMO $SRC messages --space "$TEST_SPACE" --json
  run "$tag messages --csv"               $MAJORDOMO $SRC messages --space "$TEST_SPACE" --csv
  local mname
  mname=$($MAJORDOMO $SRC messages --space "$TEST_SPACE" --window all --json 2>/dev/null \
            | python3 -c 'import sys,json; r=json.load(sys.stdin)["rows"]; print(r[0]["name"] if r else "")' 2>/dev/null)
  if [ -n "${mname:-}" ]; then
    run "$tag messages --thread <derived>" $MAJORDOMO $SRC messages --thread "$mname"
  else
    echo "no message in $TEST_SPACE to derive a thread from" > "$ERRF"
    fail "$tag messages --thread (could not derive a thread)"
  fi
}

# Live reads burn Google's per-project read quota, so the live leg is scoped to the
# test fixtures (the reason they exist) and exercises each command once, not the
# full window/format cross-product (already covered on cache).
live_matrix() {
  echo "── live backend (scoped to fixtures, quota-limited) ─────"
  run "live spaces"                       $MAJORDOMO --live spaces
  run "live spaces --json"                $MAJORDOMO --live spaces --json
  run "live spaces --csv"                 $MAJORDOMO --live spaces --csv
  run "live tasks --space TEST_SPACE"     $MAJORDOMO --live tasks --space "$TEST_SPACE"
  run "live tasks --space --json"         $MAJORDOMO --live tasks --space "$TEST_SPACE" --json
  run "live tasks --space --csv"          $MAJORDOMO --live tasks --space "$TEST_SPACE" --csv
  run "live messages --space TEST_SPACE"  $MAJORDOMO --live messages --space "$TEST_SPACE"
  run "live messages --space TEST_DM"     $MAJORDOMO --live messages --space "$TEST_DM"
  run "live messages --space --window all" $MAJORDOMO --live messages --space "$TEST_SPACE" --window all
  run "live messages --space --json"      $MAJORDOMO --live messages --space "$TEST_SPACE" --json
  run "live messages --space --csv"       $MAJORDOMO --live messages --space "$TEST_SPACE" --csv
  local mname
  mname=$($MAJORDOMO --live messages --space "$TEST_SPACE" --window all --json 2>/dev/null \
            | python3 -c 'import sys,json; r=json.load(sys.stdin)["rows"]; print(r[0]["name"] if r else "")' 2>/dev/null)
  if [ -n "${mname:-}" ]; then
    run "live messages --thread <derived>" $MAJORDOMO --live messages --thread "$mname"
  else
    echo "no message in $TEST_SPACE to derive a thread from" > "$ERRF"
    fail "live messages --thread (could not derive a thread)"
  fi
}

echo "majordomo deployment-readiness gate"
command -v "$MAJORDOMO" >/dev/null 2>&1 || { echo "  FAIL  $MAJORDOMO not on PATH"; echo "PASS 0  FAIL 1"; exit 1; }
infra_up || abort_retry "preflight" "DNS for Google/DB hosts not resolving at start."

# ---- configuration files (local, no network) --------------------------------
echo "── configuration ────────────────────────────────────────"
check "config.toml present and valid TOML" \
  python3 -c "import tomllib; tomllib.load(open('$CFGDIR/config.toml','rb'))"
check "[me].user_id set (needed by --to-me/--by-me)" \
  python3 -c "import tomllib,sys; d=tomllib.load(open('$CFGDIR/config.toml','rb')); sys.exit(0 if (d.get('me') or {}).get('user_id') else 1)"
check ".env present (cache DB connection)" test -f "$CFGDIR/.env"
check "client_secret.json present with client_id" \
  python3 -c "import json,sys; d=json.load(open('$CFGDIR/client_secret.json')); sys.exit(0 if d.get('installed',{}).get('client_id') else 1)"
check "token.json present with refresh_token" \
  python3 -c "import json,sys; d=json.load(open('$CFGDIR/token.json')); sys.exit(0 if d.get('refresh_token') else 1)"

# ---- front doors / guards ---------------------------------------------------
echo "── front doors / guards ─────────────────────────────────"
run "install-claude-command"             $MAJORDOMO install-claude-command
if $MAJORDOMO --live --cache spaces >/dev/null 2>"$ERRF"; then
  fail "--live --cache must be rejected but exited 0"
else
  pass "--live --cache rejected (guard)"
fi
if timeout 3 $MAJORDOMO mcp </dev/null >/dev/null 2>"$ERRF"; then
  pass "mcp boots (clean EOF exit)"
elif [ "$?" = 124 ]; then
  pass "mcp boots (ran until timeout)"
else
  fail "mcp cannot start"
fi

# ---- live credentials (login proxy: login is interactive, its product is a token) ----
echo "── live credentials ─────────────────────────────────────"
run "live auth works (token mints / refreshes)" $MAJORDOMO --live spaces --json

# ---- fixtures exist on the live API -----------------------------------------
LIVE_IDS=$($MAJORDOMO --live spaces --json 2>/dev/null \
            | python3 -c 'import sys,json; print(" ".join(r["space_name"] for r in json.load(sys.stdin)["rows"]))' 2>/dev/null)
if echo " $LIVE_IDS " | grep -qw "$TEST_SPACE"; then pass "TEST_SPACE $TEST_SPACE exists on live"
else echo "not in live space list (wrong id, no access, or auth down)" > "$ERRF"; fail "TEST_SPACE $TEST_SPACE exists on live"; fi
if echo " $LIVE_IDS " | grep -qw "$TEST_DM"; then pass "TEST_DM $TEST_DM exists on live"
else echo "not in live space list (wrong id, no access, or auth down)" > "$ERRF"; fail "TEST_DM $TEST_DM exists on live"; fi

# ---- read matrices ----------------------------------------------------------
matrix "--cache" "cache"
matrix ""        "auto"
live_matrix

# ---- daily-DM freshness -----------------------------------------------------
echo "── DM freshness ─────────────────────────────────────────"
newest=$($MAJORDOMO --live messages --space "$TEST_DM" --window 7d --json 2>/dev/null \
          | python3 -c 'import sys,json; r=json.load(sys.stdin)["rows"]; print(max((x.get("create_time") or "") for x in r) if r else "")' 2>/dev/null)
if python3 - "$newest" "$DM_FRESH_DAYS" 2>/dev/null <<'PY'
import sys,datetime
ts=(sys.argv[1] or "").replace("Z","+00:00")
if not ts: sys.exit(1)
try: dt=datetime.datetime.fromisoformat(ts).replace(tzinfo=None)
except ValueError: sys.exit(1)
sys.exit(0 if (datetime.datetime.utcnow()-dt).days <= int(sys.argv[2]) else 1)
PY
then pass "TEST_DM fresh (newest ${newest:-none})"
else echo "newest '${newest:-none}' older than ${DM_FRESH_DAYS}d, or none in 7d" > "$ERRF"; fail "TEST_DM fresh"; fi

# The one unavoidably global read goes last: it scans every space and is the most
# likely to hit the read quota, so a 429 here retry-aborts after everything else ran.
echo "── live people (global scan, quota-sensitive) ───────────"
run "live people --window 7d" $MAJORDOMO --live people --window 7d

echo "──────────────────────────────────────────────────────────"
printf 'PASS %d  FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
