"""Retain installed distribution notices beside the redistributable app."""
import importlib.metadata
from pathlib import Path
import shutil
import sys


def collect(target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for name in ('pywebview', 'pythonnet', 'clr_loader', 'cffi', 'pycparser', 'proxy_tools', 'bottle', 'typing_extensions', 'PyYAML'):
        distribution = importlib.metadata.distribution(name)
        information = target / distribution.metadata['Name'] / 'METADATA.txt'
        information.parent.mkdir(parents=True, exist_ok=True)
        information.write_text(distribution.read_text('METADATA') or '', encoding='utf-8')
        for member in distribution.files or []:
            basename = Path(member).name.lower()
            if basename.startswith(('license', 'copying', 'copyright', 'notice')):
                source = Path(distribution.locate_file(member))
                if source.is_file():
                    destination = target / distribution.metadata['Name'] / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    for filename in ('LICENSE.txt', 'LICENSE'):
        source = Path(sys.base_prefix) / filename
        if source.is_file():
            shutil.copy2(source, target / 'Python-LICENSE.txt')
            break


if __name__ == '__main__':
    collect(sys.argv[1])
