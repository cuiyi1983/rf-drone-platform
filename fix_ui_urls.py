#!/usr/bin/env python3
import sys

path = '/home/ubuntu/rf-drone-platform-test/tests/system/test_repeater_ui.py'
with open(path, 'r') as f:
    content = f.read()

print('Before:', content.count('localhost:5173'), '5173,', content.count('localhost:5174'), '5174')

content = content.replace('page.goto("http://localhost:5173", timeout=10000)',
                         'page.goto("http://localhost:5100/static", timeout=10000)')
content = content.replace('page.goto("http://localhost:5174", timeout=10000)',
                         'page.goto("http://localhost:5100/static", timeout=10000)')

print('After:', content.count('localhost:5173'), '5173,', content.count('localhost:5174'), '5174')

with open(path, 'w') as f:
    f.write(content)
print('[OK] saved')
