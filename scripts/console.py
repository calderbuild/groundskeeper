"""Launch the verification console."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from groundskeeper.server import serve

if __name__ == "__main__":
    serve(port=int(os.environ.get("GROUNDSKEEPER_PORT", "8765")))
