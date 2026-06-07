#!/usr/bin/env bash
# Run k6 load tests against Kiro v3

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
K6_DURATION="${K6_DURATION:-10m}"
K6_VUS="${K6_VUS:-100}"

echo "=== Kiro v3 Load Test ==="
echo "Target: $BASE_URL"
echo "Duration: $K6_DURATION"
echo "VUs: $K6_VUS"
echo

if ! command -v k6 &> /dev/null; then
    echo "k6 not found. Install: https://k6.io/docs/get-started/installation/"
    exit 1
fi

k6 run \
    --env BASE_URL="$BASE_URL" \
    -e K6_DURATION="$K6_DURATION" \
    -e K6_VUS="$K6_VUS" \
    tests/load/k6_scenario.js

echo "=== Load test complete ==="
