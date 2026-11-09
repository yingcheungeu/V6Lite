import sys
from pathlib import Path

# allow `python -m pytest v6lite/tests` from the repository root
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
