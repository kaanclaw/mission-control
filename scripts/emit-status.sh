#!/bin/bash
AGENT_ID=${1:-claw}; STATUS=${2:-idle}; TASK=${3:-"Standing by"}
MC_SERVER=${MC_SERVER:-http://localhost:8090}
curl -s -X POST "$MC_SERVER/update" -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"status\":\"$STATUS\",\"task\":\"$TASK\"}" > /dev/null \
  && echo "[$AGENT_ID] $STATUS: $TASK"
