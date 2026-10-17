"""Audit staged/tracked publication files, without reading local credentials."""
import gzip
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'outputs', '.delegation', '.venv', '.build', 'build', 'dist', 'records', '__pycache__'}
TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9_-]{30,})')


def main():
    listed = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout.decode('utf-8')
    paths = [Path(name) for name in listed.split('\0') if name]
    errors = []
    for relative in paths:
        target = ROOT / relative
        if FORBIDDEN_PARTS.intersection(relative.parts) or relative.name.startswith('.env') or relative.name == 'mesh.db' or target.stat().st_size > 50 * 1024 * 1024:
            errors.append(str(relative) + ': forbidden path or large resource')
            continue
        try:
            text = gzip.decompress(target.read_bytes()).decode('utf-8') if relative.suffix == '.gz' else target.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        if TOKEN.search(text):
            errors.append(str(relative) + ': credential-like text; inspect without printing it')
        if re.search(r'[A-Za-z]:[\\/]+Users[\\/]+\d{4,}', text):
            errors.append(str(relative) + ': personal machine path')
    if errors:
        raise SystemExit('\n'.join(errors))
    print('Publication audit passed: %s tracked files; no prohibited run/config paths or credential-like values.' % len(paths))


if __name__ == '__main__':
    main()
