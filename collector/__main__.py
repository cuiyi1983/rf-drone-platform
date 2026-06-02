"""Allow: python -m collector.api"""
from collector.api import create_app
import logging
import os
import argparse

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

parser = argparse.ArgumentParser(description="Collector Service")
parser.add_argument("--port", type=int, default=5101, help="HTTP port")
args = parser.parse_args()

app = create_app()
app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)
