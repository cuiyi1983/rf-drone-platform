#!/usr/bin/env python3
"""Fix test_repeater_ui.py skip logic - rewrite cleanly."""

path = '/home/ubuntu/rf-drone-platform-test/tests/system/test_repeater_ui.py'
with open(path) as f:
    content = f.read()

# Check if already fixed
if 'pytestmark = pytest.mark.skipif' in content:
    print('[SKIP] already fixed')
    exit(0)

# The file is corrupted - we need to remove broken skip insertions and add clean ones
# Step 1: Remove ALL occurrences of "skip_if_no_frontend()" that are wrongly placed
content = content.replace('        skip_if_no_frontend()\n', '')
content = content.replace('    skip_if_no_frontend()\n', '')

# Step 2: Add clean skip marker after the import block (module level)
import_block_end = content.find('\n\ndef test_')
if import_block_end == -1:
    import_block_end = content.find('\n\nclass ')

skip_block = '''
import os

# Skip all UI tests on CI when frontend is not available (not in git)
pytestmark = pytest.mark.skipif(
    not os.path.isdir(os.path.join(os.path.dirname(__file__), '..', '..', 'frontend')),
    reason="frontend/ not available in CI (not tracked in git)"
)

'''

content = content[:import_block_end] + skip_block + content[import_block_end:]

with open(path, 'w') as f:
    f.write(content)
print('[OK] clean pytestmark skip added')
