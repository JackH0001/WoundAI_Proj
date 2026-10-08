"""Include medical image provenance tests in the isolated Windows runner."""
import runpy
import sys
from pathlib import Path
TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
if __name__ == "__main__":
    runpy.run_path(str(TOOLS / "test_medical_image_build.py"), run_name="__main__")
