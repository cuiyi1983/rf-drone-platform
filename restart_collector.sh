#!/bin/bash
set -e
cd /repo

echo "=== Checking port 5101 ==="
# Try to free port 5101 using fuser
fuser -k 5101/tcp 2>/dev/null && echo "fuser killed process on 5101" || echo "fuser: no process on 5101"
sleep 2

echo "=== Starting collector ==="
python -m collector.api --mock-devices --port 5101 > /repo/logs/collector.log 2>&1 &
COL_PID=$!
echo "Collector PID: $COL_PID"
echo $COL_PID > /repo/collector.pid

sleep 3

echo "=== Checking collector status ==="
if kill -0 $COL_PID 2>/dev/null; then
    echo "Collector is ALIVE (PID $COL_PID)"
else
    echo "Collector is DEAD"
fi

echo "=== Last 10 lines of collector.log ==="
tail -10 /repo/logs/collector.log

echo "=== Port check ==="
python /repo/check_ports.py