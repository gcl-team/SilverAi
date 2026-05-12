import sys
from pathlib import Path

demo_dir = str(Path(__file__).resolve().parents[1])

# Ensure the demo directory is on sys.path so the test harness can load modules
# via importlib.util.spec_from_file_location without mutating sys.path twice.
if demo_dir not in sys.path:
	sys.path.insert(0, demo_dir)
