#!/usr/bin/env python3
"""Audit the Git index before publishing; never scan or print credential values.

Run after git add. This complements manual review; it is not a legal opinion
or a comprehensive secret detector. CI runs the same checks on the checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_EXTENSIONS = {
    '.mod', '.mif', '.dat', '.idx', '.dll', '.exe', '.so', '.dylib', '.bin',
    '.rom', '.qcp', '.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2', '.xz',
    '.wav', '.mid', '.sf2', '.pem', '.key', '.p12', '.pfx', '.pyc',
}
BLOCKED_PARTS = {
    'bioshock3d_game_files', 'bio3d_versions', '.venv', '__pycache__',
    '.pytest_cache', '.unlazy', '.git', 'saves', '.idea', '.vscode',
}
SECRET_PATTERNS = [
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(rb'github_pat_[A-Za-z0-9_]{40,}'),
    re.compile(rb'AKIA[0-9A-Z]{16}'),
    re.compile(rb'(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*[\"\x27][A-Za-z0-9_/+\-]{24,}[\"\x27]'),
]

# Pages that host or hand out game files; the piracy policy forbids linking them.
# Archived snapshots of documentation (web.archive.org) remain allowed.
GAME_SOURCE_LINK = re.compile(rb'https?://(?:www\.)?archive\.org/(?:details|download)/', re.IGNORECASE)


def command(*args):
    return subprocess.check_output(args, cwd=ROOT)


def violations(name, data, mode='100644'):
    path = PurePosixPath(name)
    issues = []
    if mode not in {'100644', '100755'}:
        issues.append('symlink or submodule requires manual review')
    if (path.suffix.lower() in BLOCKED_EXTENSIONS or
            any(part.lower() in BLOCKED_PARTS for part in path.parts) or
            path.name.lower() in {'credentials.json', 'launcher.json', '.ds_store'} or
            (path.name.lower().startswith('.env') and path.name != '.env.example')):
        issues.append('game file, archive, credential file or local artifact')
    if len(data) >= 50 * 1024 * 1024:
        issues.append('file exceeds the project publishing size limit (50 MiB)')
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        issues.append('possible credential material')
    if GAME_SOURCE_LINK.search(data):
        issues.append('link to a game-file download source')
    return issues


def audit():
    # Known unsafe controls prove the negative checks are actually active.
    controls = [
        ('elsewhere/bio3d.idx', b'index'),
        ('bioshock3d_game_files/readme.txt', b'originals'),
        ('.env', b'anything'),
        ('src/config.py', b'token="ghp_' + b'X' * 36 + b'"'),
        ('src/key.txt', b'-----BEGIN ' + b'PRIVATE KEY-----'),
        ('docs/notes.md', b'[builds](https://' + b'archive.org/details/example)'),
    ]
    assert all(violations(name, data) for name, data in controls), 'Unsafe control escaped detection'
    entries = command('git', 'ls-files', '--stage', '-z').split(b'\0')
    checked = 0
    failures = []
    for entry in filter(None, entries):
        metadata, raw_name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode().split()
        name = raw_name.decode()
        if stage != '0':
            failures.append(f'{name}: unresolved merge')
            continue
        data = command('git', 'cat-file', 'blob', oid)
        failures.extend(f'{name}: {issue}' for issue in violations(name, data, mode))
        checked += 1
    if not checked:
        raise RuntimeError('Git index is empty; stage the reviewed files first')
    if failures:
        raise RuntimeError('\n'.join(failures))
    # Check ignored paths even if the files are not present in this checkout.
    protected = ['bioshock3d_game_files/example.txt', 'bio3d_versions/example.txt',
                 'originals/bio3d.mod', 'originals/bio3d.idx', 'originals/bio1.dat',
                 'originals/build.zip', 'originals/bio3d.dll', '.env', '.venv/example']
    result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin'],
                            input='\n'.join(protected) + '\n', cwd=ROOT,
                            text=True, capture_output=True, check=False)
    assert set(result.stdout.splitlines()) == set(protected), 'Ignore rules leave an unsafe path exposed'
    print(f'PUBLISH AUDIT PASSED ({checked} indexed files; unsafe controls rejected)')


def media():
    from PIL import Image, ImageSequence

    readme = (ROOT / 'README.md').read_text()
    for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', readme):
        if not target.startswith(('http:', 'https:')):
            assert (ROOT / target).is_file(), f'Broken README image: {target}'
    banner = ROOT / 'docs/media/banner.png'
    with Image.open(banner) as image:
        assert image.format == 'PNG'
        assert image.width >= 1200, 'Banner must be readable at README width'
        assert 2.5 <= image.width / image.height <= 3.3, 'Banner must use a panoramic layout'
        image.verify()
    assert 'docs/media/banner.png' in readme
    for profile, size in [('vx8350', (176, 220)), ('n7705', (320, 240))]:
        gif = ROOT / f'docs/media/gameplay-{profile}.gif'
        assert str(gif.relative_to(ROOT)) in readme
        with Image.open(gif) as image:
            assert image.size == (size[0] * 2, size[1] * 2)
            hashes = {hashlib.sha256(frame.convert('RGB').tobytes()).digest()
                      for frame in ImageSequence.Iterator(image)}
            assert len(hashes) >= 4, f'Static gameplay: {profile}'
            assert image.n_frames >= 20
        with Image.open(ROOT / f'docs/media/gameplay-{profile}.png') as still:
            assert still.size == size
    print('README MEDIA PASSED (banner, linked images and changing gameplay in both editions)')


def remote(repository):
    metadata = json.loads(command('gh', 'repo', 'view', repository, '--json',
                                  'visibility,defaultBranchRef,url'))
    assert metadata['visibility'] == 'PUBLIC'
    branch = metadata['defaultBranchRef']['name']
    local = command('git', 'rev-parse', 'HEAD').decode().strip()
    remote_sha = command('git', 'ls-remote', 'origin', f'refs/heads/{branch}').decode().split()[0]
    assert remote_sha == local, 'Local commit differs from remote default branch'
    assert not command('git', 'status', '--porcelain'), 'Uncommitted files remain'
    print(f'REMOTE PUBLICATION PASSED ({metadata["url"]}, {branch}, {local[:12]})')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media', action='store_true')
    parser.add_argument('--remote', metavar='OWNER/REPO')
    args = parser.parse_args()
    if args.media:
        media()
    elif args.remote:
        remote(args.remote)
    else:
        audit()


if __name__ == '__main__':
    try:
        main()
    except (AssertionError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
