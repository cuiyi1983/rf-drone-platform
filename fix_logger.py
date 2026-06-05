#!/usr/bin/env python3
import re

with open('/home/ubuntu/rf-drone-platform-test/backend/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find lines with logger.info containing corrupted text near "sim-inference"
lines = content.split('\n')
fixed = []
found = False
for i, line in enumerate(lines):
    if 'logger.info' in line and 'sim-inference' in line and ('Platform' in line):
        # Replace with clean version
        fixed.append('                    logger.info(f"Platform: found built-in component sim-inference")')
        found = True
    else:
        fixed.append(line)

if found:
    with open('/home/ubuntu/rf-drone-platform-test/backend/main.py', 'w', encoding='utf-8') as f:
        f.write('\n'.join(fixed))
    print('[OK] fixed logger.info lines')
else:
    print('[SKIP] no corrupted lines found')
