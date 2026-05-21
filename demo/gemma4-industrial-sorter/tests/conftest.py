import importlib.util
import sys
from pathlib import Path
from types import ModuleType

demo_dir = str(Path(__file__).resolve().parents[1])

# Ensure the demo directory is on sys.path so the test harness can load modules
# via importlib.util.spec_from_file_location without mutating sys.path twice.
if demo_dir not in sys.path:
    sys.path.insert(0, demo_dir)


def load_demo_module(module_name: str, file_name: str) -> ModuleType:
    module_path = Path(demo_dir) / file_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
