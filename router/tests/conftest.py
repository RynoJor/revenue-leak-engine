import sys
from pathlib import Path

# route.py lives one level up and is run as a script, not installed as a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
