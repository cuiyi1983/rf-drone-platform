#!/usr/bin/env python3
import requests, time

PLATFORM_URL = "http://localhost:5100"

# Start session
resp = requests.post(f'{PLATFORM_URL}/api/v1/session/start', json={
    'component_id': 'sim-inference',
    'config': {
        'cf': 5805,
        'sr': 60,
        'gn': 20,
        'iq_file_path': '/repo/IQ-Record/noise_5db_600k.bin',
        'loop_play': True
    }
})
print(f'Start: {resp.status_code} {resp.json()}')
sid = resp.json().get("session_id")
print(f'session_id={sid}')

# Wait 
print('Waiting 1s...')
time.sleep(1)

# Stop
resp = requests.post(f'{PLATFORM_URL}/api/v1/session/stop', json={'session_id': sid})
print(f'Stop: {resp.status_code} {resp.json()}')
stats = resp.json().get("stats", {})
print(f'stats: {stats}')