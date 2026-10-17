"""Source and frozen entry point for Evidence Workbench."""
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent / 'app' / '医工交叉' / '循证工作台'
if SOURCE.exists():
    sys.path.insert(0, str(SOURCE))

from desktop_app import main

if __name__ == '__main__':
    raise SystemExit(main())
