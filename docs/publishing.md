# Publishing the source repository

This repository contains the Python BREW runtime, launcher, development
tools, tests, generated launcher artwork and documentation. Bring your own
matching original game files to play.

Keep these local:

| Files | Reason |
| --- | --- |
| `bioshock3d_game_files/`, `bio3d_versions/` | Original game collections |
| `.mod`, `.mif`, `.idx`, `.dat`, Windows game `.dll` files | Original executable modules, metadata, assets and saves |
| ZIP, RAR, 7z and other archives | Can contain original games or SDKs |
| Extracted QCP/WAV/MIDI audio and SoundFonts | Original or separately licensed content |
| BREW SDK headers and SDK distributions | Separately supplied development materials |
| `.env`, keys, credentials, local launcher settings | Private configuration |
| `.venv/`, caches, `.unlazy/`, planning and local verification ledgers | Machine-specific development state |

The ignored collection remains on disk. Ignore rules also cover individual
game-file extensions outside that collection. Do not use `git add -f` for
these files. The commit audit also rejects blocked paths that were force-added.

The presentation assets under `bio3dpc/ui_assets/` are generated artwork.
The GIFs and PNGs under `docs/media/` are rendered gameplay captures, not
extracted game resources. `banner.svg` is an original vector illustration.
`bio3dpc/ifaces_gen.py` records interface names and method ordering; the
generator does not copy SDK implementation or header source text.

Before every push:

```bash
git add .
.venv/bin/python tools/check_publish.py
.venv/bin/python tools/check_publish.py --media
.venv/bin/python -m pytest -q
git diff --cached --stat
git diff --cached
```

The audit examines staged blobs, rejects symlinks and files of 50 MiB or more,
checks common credential signatures, and exercises known unsafe controls to
verify its exclusions. Review new files yourself as well: extension rules
and secret patterns cannot establish ownership or detect every secret.
The CI workflow runs the same audit and tests on each push and pull request.
