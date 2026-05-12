import sys
from pathlib import Path

# Ensure the demo directory is on sys.path so that codes under
# gemma4-industrial-sorter can all be imported when the test harness
# loads modules via importlib.util.spec_from_file_location.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
