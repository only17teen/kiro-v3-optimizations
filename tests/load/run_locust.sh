#!/usr/bin/env bash
# Run Locust load tests

set -euo pipefail

HOST="${HOST:-http://localhost:8080}"
USERS="${USERS:-100}"
RATE="${RATE:-10}"
DURATION="${DURATION:-5m}"

echo "=== Kiro v3 Locust Load Test ==="
echo "Host: $HOST"
echo "Users: $USERS"
echo "Spawn rate: $RATE"
echo "Duration: $DURATION"
echo

if ! command -v locust &> /dev/null; then
    echo "Locust not found. Install: pip install locust"
    exit 1
fi

locust -f tests/load/locustfile.py \
    --host "$HOST" \
    --users "$USERS" \
    --spawn-rate "$RATE" \
    --run-time "$DURATION" \
    --headless \
    --html reports/locust_report.html \
    --csv reports/locust

echo "=== Locust test complete ==="
