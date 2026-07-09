#!/bin/bash
# Monitor a GoA run via the API dashboard endpoint
# Usage: ./scripts/monitor_run.sh run_57vd316xyfk16q [interval_seconds]

RUN_ID="${1:-run_57vd316xyfk16q}"
INTERVAL="${2:-300}"
API="http://localhost:8000"
TOKEN="dev-token"
LOG=".goa_data/monitor_${RUN_ID}.log"

echo "Monitoring $RUN_ID every ${INTERVAL}s → $LOG"

while true; do
    RESULT=$(curl -s "$API/runs/$RUN_ID/dashboard" -H "Authorization: Bearer $TOKEN" 2>/dev/null)
    STATUS=$(echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('run',{}).get('status','?'))" 2>/dev/null)

    SUMMARY=$(echo "$RESULT" | python3 -c "
import json, sys
from datetime import datetime
d = json.load(sys.stdin)
run = d.get('run', {})
state = run.get('state', {})
ts = datetime.now().strftime('%Y-%m-%d %H:%M')
agents = len(state.get('agents', {}))
bots = len(state.get('bots', {}))
offers = len(state.get('offers', {}))
purchases = len(state.get('purchases', {}))
chat = len(state.get('comments', state.get('chat', [])))
print(f'[{ts}] status={run.get(\"status\")} agents={agents} bots={bots} offers={offers} purchases={purchases} chat={chat}')
" 2>/dev/null)

    echo "$SUMMARY" | tee -a "$LOG"

    if [ "$STATUS" = "finished" ] || [ "$STATUS" = "failed" ]; then
        echo "Run $STATUS — stopping monitor" | tee -a "$LOG"
        break
    fi

    sleep "$INTERVAL"
done
