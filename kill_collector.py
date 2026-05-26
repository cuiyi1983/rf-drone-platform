import socket
import os
import signal

# Find all python processes
import subprocess
result = subprocess.run(['find', '/proc', '-maxdepth', '1', '-name', '[0-9]*'], capture_output=True, text=True)
pids = [int(p.split('/')[-1]) for p in result.stdout.strip().split('\n') if p]

collector_pids = []
for pid in pids:
    try:
        with open(f'/proc/{pid}/cmdline', 'r') as f:
            cmdline = f.read()
            if 'collector.api' in cmdline or 'collector' in cmdline:
                collector_pids.append(pid)
                print(f"Found collector PID: {pid} -> {cmdline[:100]}")
    except:
        pass

# Kill all collector processes
for pid in collector_pids:
    try:
        os.kill(pid, signal.SIGKILL)
        print(f"Killed PID {pid}")
    except Exception as e:
        print(f"Failed to kill {pid}: {e}")

# Also kill by port
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('0.0.0.0', 5101))
    print("Port 5101 now FREE")
except Exception as e:
    print(f"Port 5101 still in use: {e}")
finally:
    s.close()