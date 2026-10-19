# Build with Python 3.12 x64 on Windows, from the repository root.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
project = root / 'app' / '医工交叉'
workbench = project / '循证工作台'
mesh = project / 'mesh-query'
agent = project / '学术智能体'
sys.path.insert(0, str(mesh))
datas = []

def include(path):
    datas.append((str(path), str(Path('workspace') / '医工交叉' / path.parent.relative_to(project))))

for name in ('index.html', 'presentation.css'):
    include(workbench / name)
include(mesh / 'data' / 'mesh.db')
include(mesh / 'data' / 'zh_map.json')
include(project / 'sq3r-skill' / 'skill' / 'SKILL.md')
for path in (workbench / 'samples').glob('*'):
    if path.suffix in {'.gz', '.json'}:
        include(path)
for path in (project / 'academic-agent-v2' / '学术智能体' / 'src').glob('*.py'):
    include(path)
# health() checks for this readable resource; the import is also frozen below.
include(agent / 'agent.py')

imports = ['desktop_app', 'runtime_paths', 'http_service', 'orchestrator', 'research_engine',
           'demo_data', 'evidence_policy', 'run_store', 'workbench_jobs',
           'agent', 'sources', 'miniyaml', 'webview.platforms.winforms', 'webview.platforms.edgechromium']
imports += collect_submodules('mesh_query')
a = Analysis([str(root / 'run.py')], pathex=[str(workbench), str(agent), str(mesh)],
             binaries=[], datas=datas, hiddenimports=imports,
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'cefpython3',
                       'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa',
                       'webview.platforms.android'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='EvidenceWorkbench',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon=str(root / 'assets' / 'icon.ico'),
          version=str(root / 'packaging' / 'version_info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='EvidenceWorkbench')
