#!/bin/bash
cd /repo

echo "=== Running TCP tests ==="

echo "[1] test_collector_tcp_real.py"
python tests/test_collector_tcp_real.py > /tmp/r1.log 2>&1
R1=$?
echo "Exit code: $R1"
tail -20 /tmp/r1.log

echo ""
echo "[2] test_tcp_connection_timing.py"
python tests/test_tcp_connection_timing.py > /tmp/r2.log 2>&1
R2=$?
echo "Exit code: $R2"
tail -20 /tmp/r2.log

echo ""
echo "=== Summary ==="
echo "test_collector_tcp_real.py: $([ $R1 -eq 0 ] && echo 'PASS' || echo 'FAIL')"
echo "test_tcp_connection_timing.py: $([ $R2 -eq 0 ] && echo 'PASS' || echo 'FAIL')"

# Save results
{
  echo "=== TCP Connection Test Results ==="
  echo "Date: $(date)"
  echo ""
  echo "--- test_collector_tcp_real.py ---"
  cat /tmp/r1.log
  echo ""
  echo "--- test_tcp_connection_timing.py ---"
  cat /tmp/r2.log
  echo ""
  echo "=== Summary ==="
  echo "test_collector_tcp_real.py: $([ $R1 -eq 0 ] && echo 'PASS' || echo 'FAIL')"
  echo "test_tcp_connection_timing.py: $([ $R2 -eq 0 ] && echo 'PASS' || echo 'FAIL')"
} > /repo/test_results.txt

echo "Results saved to /repo/test_results.txt"