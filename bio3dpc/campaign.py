"""Rebuild the Nokia 7705 landscape campaign packs from sibling original data.

The Nokia v1.5.2.1 release folder ships only ``bio1.dat`` and ``bio2.dat``;
its ``bio3d.idx`` still names all seven packs and their sizes.  The retail LG
VX9900 data set is the same 320x240 content: its bio1/bio2 differ from Nokia's
only by phone-key labels in text, and its bio3 and bio6 have exactly the sizes
Nokia's index declares.  Nokia relabeled VX9900's QWERTY prompts for a keypad
(``(W)`` -> ``(0)``, ``(Z)`` -> ``(OK)``).  Applying that to the later level
prompts reproduces Nokia's declared bio4 size exactly; VX8350's bio5 has
Nokia's bio5 size exactly.  Only bio7 cannot be size-matched (335 bytes larger),
so the rebuilt index declares its real size, which the game checks before
using a pack.

Every input and output is pinned by SHA-256.  Originals are only read; the
result is written to a separate folder together with copies of the Nokia
starter packs, so it can be selected as an ordinary data folder.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import struct

from .profiles import N7705, VX8350, discover_root

LABELS = ((b'(W)', b'(0)'), (b'(Z)', b'(OK)'))


@dataclass(frozen=True)
class PackRecipe:
    name: str
    donor: str            # 'vx9900' or 'vx8350'
    donor_sha256: str
    sha256: str           # expected rebuilt pack
    relabel: bool = False


RECIPE = (
    PackRecipe('bio3.dat', 'vx9900', 'fc2dacbbedebbfdda0335bbc0b2c9e3534867134edfc4843fbb3d1619f1b7d96',
               'fc2dacbbedebbfdda0335bbc0b2c9e3534867134edfc4843fbb3d1619f1b7d96'),
    PackRecipe('bio4.dat', 'vx9900', '41716dd4362af1b316e6c9e6cc22954f7ece2f46af33fe8e65005c3722122654',
               '33aa85353b1ed09a510de7a80981f75d3f2cb5722d0a02368d9d16dba7d92df2', relabel=True),
    PackRecipe('bio5.dat', 'vx8350', '103257778924a75dcd73df5a56c2cd46f4acdf0cdf5ee463f9e5cf4ca3a8710b',
               '103257778924a75dcd73df5a56c2cd46f4acdf0cdf5ee463f9e5cf4ca3a8710b'),
    PackRecipe('bio6.dat', 'vx9900', 'c1e6632cbcaf826e08f3d444a99fa32c0b385851d39b8def266461197bb29d82',
               '30ad52557ef8aa6403dd2d970389ac76ee2024cdf9fdca94744cced96b715e35', relabel=True),
    PackRecipe('bio7.dat', 'vx9900', 'c7e0f1c03b9047f3373e1af5f6678fa45992a7f3940581392fab2f817e750a2c',
               'c7e0f1c03b9047f3373e1af5f6678fa45992a7f3940581392fab2f817e750a2c'),
)
INDEX_SHA256 = '20561e7b18ca7d1ea8d1111c6b6a3f148fcc54024e957a16ee39943d25861b89'
CAMPAIGN_HASHES = dict(N7705.asset_hashes) | {r.name: r.sha256 for r in RECIPE} | {'bio3d.idx': INDEX_SHA256}


class CampaignError(ValueError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ DAT packs
# [u32 0x00040001][u32 count][u32 priority], a directory of
# [u16][u32 size][u32 name length][name][u32 blob offset][u32 link] (the last
# record has no link), then [u16 8][u32 size][u16 type] + payload per record.

def read_pack(data: bytes) -> dict:
    magic, count, priority = struct.unpack_from('<3I', data, 0)
    offset, records = 12, []
    for index in range(count):
        lead, size, length = struct.unpack_from('<HII', data, offset)
        offset += 10
        name = data[offset:offset + length]
        offset += length
        blob = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        link = None
        if index != count - 1:
            link = struct.unpack_from('<I', data, offset)[0]
            offset += 4
        tag, stored, kind = struct.unpack_from('<HIH', data, blob)
        if tag != 8 or stored != size:
            raise CampaignError(f'Unexpected DAT record {name!r}')
        records.append(dict(lead=lead, name=name, link=link, kind=kind,
                            body=data[blob + 8:blob + 8 + size]))
    return dict(magic=magic, priority=priority, records=records)


def write_pack(pack: dict) -> bytes:
    records = pack['records']
    head = struct.pack('<3I', pack['magic'], len(records), pack['priority'])
    blob = len(head) + sum(14 + len(r['name']) + (r['link'] is not None) * 4 for r in records)
    directory, blobs = [head], []
    for record in records:
        directory.append(struct.pack('<HII', record['lead'], len(record['body']), len(record['name']))
                         + record['name'] + struct.pack('<I', blob))
        if record['link'] is not None:
            directory.append(struct.pack('<I', record['link']))
        blobs.append(struct.pack('<HIH', 8, len(record['body']), record['kind']) + record['body'])
        blob += 8 + len(record['body'])
    return b''.join(directory + blobs)


def relabel_prompts(data: bytes) -> bytes:
    """Apply Nokia's keypad labels to the in-level GMessagesLevel prompts."""
    pack = read_pack(data)
    if write_pack(pack) != data:
        raise CampaignError('DAT pack does not round-trip')
    for record in pack['records']:
        if record['name'].startswith(b'GMessagesLevel'):
            for old, new in LABELS:
                record['body'] = record['body'].replace(old, new)
    return write_pack(pack)


# ---------------------------------------------------------------- the index

def read_index(data: bytes) -> list[tuple[str, int]]:
    lines = data.decode('ascii').split('\r\n')
    if lines[0] != 'INDX':
        raise CampaignError('bio3d.idx is not a BioShock 3D index')
    count = int(lines[1])
    return [(lines[2 + 2 * i], int(lines[3 + 2 * i])) for i in range(count)]


def write_index(entries: list[tuple[str, int]]) -> bytes:
    lines = ['INDX', str(len(entries))]
    for name, size in entries:
        lines += [name, f'{size:09d}']
    return ('\r\n'.join(lines) + '\r\n').encode('ascii')


# -------------------------------------------------------------- the rebuild

def default_output() -> Path:
    base = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local/share')
    return Path(base) / 'bio3dpc' / 'campaign' / N7705.id


def donor_folders(library: Path | str | None = None) -> dict[str, Path]:
    root = Path(library) if library is not None else discover_root()
    return {'nokia': N7705.data_path(root), 'vx9900': Path(root) / 'data' / 'vx9900',
            'vx8350': VX8350.data_path(root)}


def check_donors(folders: dict[str, Path]) -> list[str]:
    problems = []
    for name, expected in N7705.asset_hashes:
        path = Path(folders['nokia']) / name
        if not path.is_file() or sha256(path) != expected:
            problems.append(f'Nokia 7705 {name} is missing or does not match: {path}')
    for recipe in RECIPE:
        path = Path(folders[recipe.donor]) / recipe.name
        if not path.is_file():
            problems.append(f'{recipe.donor.upper()} {recipe.name} is missing: {path}')
        elif sha256(path) != recipe.donor_sha256:
            problems.append(f'{recipe.donor.upper()} {recipe.name} does not match the known original: {path}')
    return problems


def verify_campaign(folder: Path | str) -> list[str]:
    folder = Path(folder)
    problems = []
    for name, expected in CAMPAIGN_HASHES.items():
        path = folder / name
        if not path.is_file():
            problems.append(f'Missing {name}')
        elif sha256(path) != expected:
            problems.append(f'{name} does not match the rebuilt landscape campaign')
    return problems


def rebuild(folders: dict[str, Path], output: Path | str | None = None) -> Path:
    """Write the complete seven-pack landscape data folder and verify it."""
    output = Path(output) if output is not None else default_output()
    if any(output.resolve() == Path(folder).resolve() for folder in folders.values()):
        raise CampaignError('Choose an output folder separate from the original game files')
    if output.exists() and (not output.is_dir() or
                            any(path.name not in CAMPAIGN_HASHES for path in output.iterdir())):
        raise CampaignError(f'Refusing to replace {output}: it holds files other than a rebuilt campaign')
    problems = check_donors(folders)
    if problems:
        raise CampaignError('\n'.join(problems))
    staging = output.with_name(output.name + '.partial')
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    nokia = Path(folders['nokia'])
    for name in ('bio1.dat', 'bio2.dat'):
        shutil.copyfile(nokia / name, staging / name)
    for recipe in RECIPE:
        data = (Path(folders[recipe.donor]) / recipe.name).read_bytes()
        (staging / recipe.name).write_bytes(relabel_prompts(data) if recipe.relabel else data)
    sizes = {path.name: path.stat().st_size for path in staging.glob('bio*.dat')}
    entries = read_index((nokia / 'bio3d.idx').read_bytes())
    (staging / 'bio3d.idx').write_bytes(write_index([(name, sizes[name]) for name, _ in entries]))
    problems = verify_campaign(staging)
    if problems:
        shutil.rmtree(staging, ignore_errors=True)
        raise CampaignError('\n'.join(problems))
    shutil.rmtree(output, ignore_errors=True)
    staging.replace(output)
    return output
