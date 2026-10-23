#!/usr/bin/env python3
"""Rebuild the Nokia 7705 landscape campaign (bio3.dat-bio7.dat) from original sibling data.

Reads, never modifies: the Nokia 278583 folder, data/vx9900 and data/vx8350 of
your collection.  Writes a complete seven-pack data folder (default
~/.local/share/bio3dpc/campaign/n7705) that the launcher uses for landscape.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bio3dpc.campaign import CampaignError, default_output, donor_folders, rebuild  # noqa: E402
from bio3dpc.profiles import discover_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--library', type=Path, default=discover_root(), help='bioshock3d_game_files collection')
    parser.add_argument('--nokia', type=Path, help='Nokia 7705 data folder (bio1.dat, bio2.dat, bio3d.idx)')
    parser.add_argument('--vx9900', type=Path, help='LG VX9900 data folder (bio3.dat-bio7.dat)')
    parser.add_argument('--vx8350', type=Path, help='LG VX8350 data folder (bio5.dat)')
    parser.add_argument('--out', type=Path, default=default_output())
    args = parser.parse_args()
    folders = donor_folders(args.library)
    for key in ('nokia', 'vx9900', 'vx8350'):
        if getattr(args, key) is not None:
            folders[key] = getattr(args, key)
    try:
        output = rebuild(folders, args.out)
    except (CampaignError, OSError) as error:
        print(f'rebuild failed:\n{error}', file=sys.stderr)
        return 1
    print(f'Landscape campaign rebuilt and verified: {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
