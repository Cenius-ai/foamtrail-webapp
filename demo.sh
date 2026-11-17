#!/usr/bin/env bash
# FoamTrail — non-interactive end-to-end demo.
#
# Runs the real CLI against a throwaway state folder so your own
# ~/.foamtrail/state.json is never touched. Registers three bags of beans,
# logs a dial-in session, then shows the bean freshness list, the shot
# history, the grind-versus-yield chart and two rejected inputs.
#
# Usage:  bash demo.sh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
foamtrail() { "$PYTHON" foamtrail.py "$@"; }

DEMO_DIR="$(mktemp -d 2>/dev/null || printf '%s' "./.foamtrail-demo")"
export FOAMTRAIL_DATA_DIR="$DEMO_DIR"
export PYTHONIOENCODING="utf-8"

days_ago() {
  "$PYTHON" -c 'import datetime,sys; print((datetime.date.today()-datetime.timedelta(days=int(sys.argv[1]))).isoformat())' "$1"
}

section() { printf '\n=== %s ===\n' "$1"; }

section "FoamTrail — espresso dial-in log"
foamtrail --version
printf 'demo state: %s\n' "$DEMO_DIR/state.json"

section "Register three bags (roast date => days off roast)"
foamtrail beans add "House Blend" --roast "$(days_ago 9)"
foamtrail beans add "El Paraiso Geisha" --roast "$(days_ago 46)"
foamtrail beans add "Huehuetenango Decaf" --roast "$(days_ago 2)"

section "Log the dial-in session"
foamtrail log --bean "House Blend" --grind 10 --dose 18 --yield 30 --time 26 --notes "sour, thin — grind finer"
foamtrail log --bean "House Blend" --grind 12 --dose 18 --yield 34 --time 28 --notes "sweeter, still bright"
foamtrail log --bean "House Blend" --grind 13 --dose 18 --yield 36 --time 29 --notes "balanced, caramel finish"
foamtrail log --bean "Huehuetenango Decaf" --grind 11 --dose 17 --yield 32 --time 27 --notes "cocoa, low acid"
foamtrail log --bean "El Paraiso Geisha" --grind 15 --dose 18 --yield 40 --time 31 --notes "flat, woody — bag is old"

section "Shot history, newest first"
foamtrail shots

section "Grind size versus yield"
foamtrail chart

section "Every bean again, now with its shot count"
foamtrail beans

section "Validation: a grind of 0 clicks is rejected and nothing is logged"
foamtrail log --grind 0 --dose 18 --yield 36 --time 28 || true

section "Validation: an impossible roast date is rejected and no bean is saved"
foamtrail beans add "Mystery" --roast 2024-13-40 || true

section "Machine-readable output (scripting with jq and friends)"
foamtrail chart --json

printf '\n=== demo complete: 3 beans, 5 shots ===\n'
printf 'The demo used a throwaway folder; your own log lives in ~/.foamtrail/state.json.\n'
