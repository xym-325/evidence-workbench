"""Fetch only the pinned MeSH asset and verify both archive and database."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', help='owner/repository; inferred from origin when omitted')
    parser.add_argument('--archive', type=Path, help='Use an already downloaded release asset')
    parser.add_argument('--authenticated', action='store_true', help='Use the logged-in GitHub CLI for a private repository')
    args = parser.parse_args()
    metadata = json.loads((ROOT / 'resources/mesh.json').read_text(encoding='utf-8'))
    target = ROOT / 'app/医工交叉/mesh-query/data/mesh.db'
    if target.exists():
        if sha256(target) == metadata['database_sha256']:
            print('MeSH resource already verified.')
            return
        raise SystemExit('Existing MeSH resource differs; preserve it and remove it manually before fetching.')
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    archive = args.archive
    if archive is None:
        repository = args.repo
        if not repository:
            origin = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=ROOT, capture_output=True, text=True)
            match = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$', origin.stdout.strip())
            repository = match.group(1) if match else None
        if not repository or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise SystemExit('Provide --repo owner/repository or --archive local-resource.gz.')
        archive = cache / metadata['asset_name']
        if args.authenticated:
            subprocess.run(['gh', 'release', 'download', metadata['release_tag'], '--repo', repository, '--pattern', metadata['asset_name'], '--dir', str(cache), '--clobber'], check=True)
        else:
            url = 'https://github.com/%s/releases/download/%s/%s' % (repository, metadata['release_tag'], metadata['asset_name'])
            with urllib.request.urlopen(url, timeout=120) as source, archive.open('wb') as destination:
                shutil.copyfileobj(source, destination)
    if sha256(archive) != metadata['archive_sha256']:
        raise SystemExit('Archive SHA-256 mismatch; resource was not installed.')
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.mesh-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as destination, gzip.open(archive, 'rb') as source:
            written = 0
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > metadata['database_bytes']:
                    raise RuntimeError('MeSH resource exceeds expected size.')
                destination.write(chunk)
        if temporary.stat().st_size != metadata['database_bytes'] or sha256(temporary) != metadata['database_sha256']:
            raise RuntimeError('Database SHA-256 mismatch; resource was not installed.')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print('Verified MeSH resource installed.')


if __name__ == '__main__':
    main()
