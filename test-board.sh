#!/usr/bin/env bash
# Cross-repo integration test: table-talk board mode against a live buildboard.
# Opt-in — requires a buildboard checkout (BUILDBOARD_REPO, default
# /home/gnava/repos/buildboard). Not part of the default test.sh.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BB="${BUILDBOARD_REPO:-/home/gnava/repos/buildboard}"
[ -d "$BB" ] || { echo "skip: buildboard not found at $BB (set BUILDBOARD_REPO)"; exit 0; }
PORT="${BUILDBOARD_TEST_PORT:-4466}"
BASE="http://localhost:$PORT"
TT="$here/bin/table-talk"

dbdir="$(mktemp -d)"
trap 'kill -9 "$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oE "pid=[0-9]+" | head -1 | cut -d= -f2 || true)" 2>/dev/null || true; rm -rf "$dbdir"' EXIT

cd "$BB"
BUILDBOARD_DB="$dbdir/board.db" nohup npx vite dev --port "$PORT" --strictPort >"$dbdir/dev.log" 2>&1 &
child=$!
for _ in $(seq 1 60); do
  curl -sf "$BASE/api/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -sf "$BASE/api/health" >/dev/null || { echo "FAIL: buildboard did not start"; exit 1; }
export BUILDBOARD_URL="$BASE"

pass=0; fail=0
ok() { echo "ok: $1"; pass=$((pass+1)); }
bad() { echo "FAIL: $1${2:+ — $2}"; fail=$((fail+1)); }

jget() { node -e "let d='';process.stdin.on('data',c=>d+=c).on('end',()=>{const v=JSON.parse(d);console.log(eval('v'+process.argv[1]))})" "$1"; }

# action -> decide
ref="$(BUILDBOARD_URL="$BASE" "$TT" action "Integrate tests?" --why "quality" --rec "yes" 2>/dev/null)"
[ -n "$ref" ] && ok "action returns a ref" || bad "action returns a ref" "$ref"
item="$(curl -s "$BASE/api/ref/$ref")"
src="$(echo "$item" | jget .source)"
[ "$src" = "item" ] && ok "ref resolves to the decision item" || bad "ref resolves to the decision item" "$src"

# blocked task + blocks edge, then resolve -> unblock
taskid="$(curl -s -X POST "$BASE/api/items" -H 'content-type: application/json' -d '{"title":"tt it","kind":"task","x":0,"y":0,"board_id":"default"}' | jget .id)"
taskref="$(curl -s "$BASE/api/items/$taskid" | jget .ref)"
did="$(echo "$item" | jget .id)"
curl -s -X PATCH "$BASE/api/items/$taskid" -H 'content-type: application/json' -d '{"status":"blocked"}' >/dev/null
curl -s -X POST "$BASE/api/edges" -H 'content-type: application/json' -d "{\"from_id\":\"$taskid\",\"to_id\":\"$did\",\"kind\":\"blocks\",\"board_id\":\"default\"}" >/dev/null

out="$(BUILDBOARD_URL="$BASE" "$TT" done "$ref" --choice "yes")"
echo "$out" | grep -q "resolved" && ok "done --choice resolves the decision" || bad "done --choice resolves the decision" "$out"
st="$(curl -s "$BASE/api/items/$(echo "$item" | jget .id)" | jget .status)"
[ "$st" = "done" ] && ok "decision item marked done" || bad "decision item marked done" "$st"
tst="$(curl -s "$BASE/api/items/$taskid" | jget .status)"
[ "$tst" = "open" ] && ok "blocked task unblocked on resolve" || bad "blocked task unblocked on resolve" "$tst"

# missing choice is rejected
r2="$(BUILDBOARD_URL="$BASE" "$TT" action "Second?" --why "x" --rec "a" 2>/dev/null)"
out="$(BUILDBOARD_URL="$BASE" "$TT" done "$r2" 2>&1 || true)"
if echo "$out" | grep -q "needs a choice"; then ok "missing choice is rejected"; else bad "missing choice is rejected" "$out"; fi

# show --open
BUILDBOARD_URL="$BASE" "$TT" show --open | grep -q "$r2" && ok "show --open lists the open decision" || bad "show --open lists the open decision"
# status
BUILDBOARD_URL="$BASE" "$TT" status | grep -q "reachable=yes" && ok "status reports reachable" || bad "status reports reachable"
# dry-run (offline, no network)
BUILDBOARD_URL="$BASE" "$TT" action "Dry?" --why w --rec r --dry-run | grep -q "dry-run" && ok "dry-run prints the call" || bad "dry-run prints the call"
# json
BUILDBOARD_URL="$BASE" "$TT" action "Json?" --why w --rec r --json | grep -q '"ref"' && ok "json emits a stable object" || bad "json emits a stable object"

# term -> glossary upsert (dedup by name)
tref="$(BUILDBOARD_URL="$BASE" "$TT" term "Cache" --intuitive "memoized store" --technical "fast lookup table" 2>/dev/null | head -1)"
tref="$(echo "$tref" | grep -oE '[0-9a-f]{4}' | head -1)"
[ -n "$tref" ] && ok "term returns a ref" || bad "term returns a ref" "$tref"
c1="$(curl -s "$BASE/api/ref/$tref")"
[ "$(echo "$c1" | jget .source)" = "concept" ] && ok "term ref resolves to a concept" || bad "term ref resolves to a concept" "$c1"
tref2="$(BUILDBOARD_URL="$BASE" "$TT" term "cache" --intuitive "memoized store" --technical "fast lookup table" 2>/dev/null | grep -oE '[0-9a-f]{4}' | head -1)"
[ "$tref2" = "$tref" ] && ok "term upserts by name (dedup)" || bad "term upserts by name (dedup)" "$tref vs $tref2"

# task -> recordTask; progress -> pct; blocked-on -> blocks edge + unblock
tref="$(BUILDBOARD_URL="$BASE" "$TT" task "ship it" 2>/dev/null | head -1)"
tref="$(echo "$tref" | grep -oE '[0-9a-f]{4}' | head -1)"
[ -n "$tref" ] && ok "task returns a ref" || bad "task returns a ref" "$tref"
titem="$(curl -s "$BASE/api/ref/$tref")"
titemid="$(echo "$titem" | jget .id)"
BUILDBOARD_URL="$BASE" "$TT" progress "$tref" "half way" --pct 40 >/dev/null 2>&1
tp="$(curl -s "$BASE/api/items/$titemid" | jget .pct)"
[ "$tp" = "40" ] && ok "progress sets pct" || bad "progress sets pct" "$tp"
BUILDBOARD_URL="$BASE" "$TT" progress "$tref" --pct 100 >/dev/null 2>&1
tst="$(curl -s "$BASE/api/items/$titemid" | jget .status)"
[ "$tst" = "done" ] && ok "pct=100 marks task done" || bad "pct=100 marks task done" "$tst"

# blocked-on an open action -> blocked; resolving the action unblocks it
bact="$(BUILDBOARD_URL="$BASE" "$TT" action "Gate?" --why w --rec y 2>/dev/null | head -1)"
bact="$(echo "$bact" | grep -oE '[0-9a-f]{4}' | head -1)"
btask="$(BUILDBOARD_URL="$BASE" "$TT" task "wait for gate" 2>/dev/null | grep -oE '[0-9a-f]{4}' | head -1)"
btaskid="$(curl -s "$BASE/api/ref/$btask" | jget .id)"
BUILDBOARD_URL="$BASE" "$TT" progress "$btask" "waiting" --blocked-on "$bact" >/dev/null 2>&1
bst="$(curl -s "$BASE/api/items/$btaskid" | jget .status)"
[ "$bst" = "blocked" ] && ok "--blocked-on sets blocked" || bad "--blocked-on sets blocked" "$bst"
BUILDBOARD_URL="$BASE" "$TT" done "$bact" --choice "y" >/dev/null 2>&1
bst2="$(curl -s "$BASE/api/items/$btaskid" | jget .status)"
[ "$bst2" = "open" ] && ok "resolving the gate unblocks the task" || bad "resolving the gate unblocks the task" "$bst2"

echo "board-mode: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
