"""Build a local Windows x64 portable app; never publishes anything."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-tests', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Windows packages must be built on Windows.')
    version = (ROOT / 'VERSION').read_text().strip()
    mesh = ROOT / 'app/医工交叉/mesh-query/data/mesh.db'
    metadata = json.loads((ROOT / 'resources/mesh.json').read_text(encoding='utf-8'))
    if not mesh.is_file() or sha256(mesh) != metadata['database_sha256']:
        raise SystemExit('MeSH resource missing or incorrect. Run scripts/fetch_mesh.py first.')
    if not args.skip_tests:
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'], cwd=ROOT / 'app/医工交叉/循证工作台', check=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', 'packaging/windows.spec'], cwd=ROOT, check=True)
    folder = ROOT / 'dist/EvidenceWorkbench'
    for source, name in [('README.md', 'README.md'), ('CHANGELOG.md', 'CHANGELOG.md'), ('THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md')]:
        shutil.copy2(ROOT / source, folder / name)
    from collect_licenses import collect
    collect(folder / 'licenses')
    archive = ROOT / 'dist' / ('EvidenceWorkbench-' + version + '-windows-x64.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(ROOT / 'dist').as_posix())
    with zipfile.ZipFile(archive) as bundle:
        bad = bundle.testzip()
        if bad:
            raise RuntimeError('Invalid package member: ' + bad)
    checksums = ROOT / 'dist' / 'SHA256SUMS.txt'
    resources = [archive]
    resource_archive = ROOT / 'dist' / metadata['asset_name']
    if resource_archive.is_file():
        resources.append(resource_archive)
    checksums.write_text(''.join(sha256(p) + '  ' + p.name + '\n' for p in resources), encoding='utf-8')
    print(json.dumps({'version': version, 'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': sha256(archive)}))


if __name__ == '__main__':
    main()
