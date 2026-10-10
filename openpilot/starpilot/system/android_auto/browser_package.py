"""Pinned browser and resolver release assets, and the runtime directory they install to."""
import json
from pathlib import Path

PACKAGE = json.loads(Path(__file__).with_suffix('.json').read_text())
RUNTIME = Path('/data/starpilot/google-browser') / PACKAGE['id']
