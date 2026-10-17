"""Read-only application resources and writable user data are separate."""
import os
from pathlib import Path
import sys

VERSION = '1.0.0'
APP_ID = 'evidence-workbench'
APP_NAME = '循证科研工作台'


def project_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'workspace' / '医工交叉'
    return Path(__file__).resolve().parent.parent


def workbench_root():
    return project_root() / '循证工作台'


def user_data_dir():
    override = os.environ.get('EVIDENCE_WORKBENCH_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'EvidenceWorkbench'


def outputs_dir():
    if getattr(sys, 'frozen', False) or os.environ.get('EVIDENCE_WORKBENCH_DATA_DIR'):
        return user_data_dir() / 'outputs'
    return workbench_root() / 'outputs'
