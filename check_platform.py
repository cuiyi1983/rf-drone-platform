#!/usr/bin/env python3
import os, sys
sys.path.insert(0, '/home/ubuntu/rf-drone-platform-test')
sys.path.insert(0, '/home/ubuntu/rf-drone-platform-test/backend')

from backend.main import create_platform
import asyncio

async def check():
    p = await create_platform()
    print("Components:", list(p._components.keys()))
    # Check if frontend dir exists
    frontend_dir = os.path.join('/home/ubuntu/rf-drone-platform-test', 'frontend')
    print("Frontend dir exists:", os.path.isdir(frontend_dir))
    if os.path.isdir(frontend_dir):
        print("Frontend files:", os.listdir(frontend_dir))

try:
    asyncio.run(check())
except Exception as e:
    print("ERROR:", e)
    import traceback; traceback.print_exc()
