import sys, time, os
sys.path.insert(0, "/repo")
from collector.api import CollectorAPI

api = CollectorAPI()
import requests

# 1. Test /devices returns pluto-repeater
devs = requests.get("http://localhost:5101/api/v1/collector/devices").json()
print("[devices]", devs)
pluto_repeater_found = any(d.get("type") == "pluto-repeater" for d in devs.get("devices", []))
print("[pluto-repeater in devices]", pluto_repeater_found)

# 2. Test /start with iq_file_path (repeater mode)
resp = requests.post("http://localhost:5101/api/v1/collector/start", json={
    "mode": "repeater",
    "config": {
        "frequencies": [5805000000],
        "buffer_size": 524288,
        "sample_rate": 60000000,
        "iq_file_path": "/repo/IQ-Record/noise_5db_600k.bin"
    }
}, timeout=5)
print("[start repeater]", resp.json())

# 3. Wait 1 sec and check status
time.sleep(1)
status = requests.get("http://localhost:5101/api/v1/collector/status").json()
print("[status]", status)