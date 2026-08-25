import os
import sys
from pathlib import Path

# Add project root to Python path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if os.environ.get("VERCEL"):
    os.environ.setdefault("DATA_DIR", "/tmp/var")

from backend.main import app

# Vercel serverless function entrypoint
