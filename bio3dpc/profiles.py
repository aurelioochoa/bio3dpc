"""Hash-identified BREW builds and their matching original asset directories."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


@dataclass(frozen=True)
class BuildProfile:
    id: str
    title: str
    width: int
    height: int
    app_clsid: int
    sha256: str
    folder: str
    module_dir: str
    data_set: str | None = None
    software: bool = True
    asset_hashes: tuple[tuple[str, str], ...] = ()

    def mod_path(self, root: Path | str) -> Path:
        root = Path(root)
        grouped = root / 'release_builds' / self.folder / self.module_dir / 'bio3d.mod'
        legacy = root / self.folder / self.module_dir / 'bio3d.mod'
        return legacy if legacy.is_file() and not grouped.is_file() else grouped

    def data_path(self, root: Path | str) -> Path:
        if self.data_set:
            candidate = Path(root) / 'data' / self.data_set
            if candidate.is_dir():
                return candidate
        return self.mod_path(root).parent


VX8350 = BuildProfile(
    'vx8350', 'LG VX8350 · Portrait', 176, 220, 0x0109005B,
    '53736c667d6635a5a6bbaecb3d63db6da047c4f6be2e6bc0135d13321c33d4fa',
    'v.1.4.1.1 - lg vx8350 - 176x220', '278595', 'vx8350', asset_hashes=(
        ('bio1.dat', 'e19995c84d42631276bcba8ebe0c4ce8f3dbf97a2285765551340232116a1e5a'),
        ('bio2.dat', 'aa68d894cdb79efe560a13b4f070f91301d982717015190d2cbfa10af2829e09'),
        ('bio3d.idx', 'f262a8aebec8130d1f1646dcdad9a512338ba3ed21275e55d2d1a0a10114a3e5')))
N7705 = BuildProfile(
    'n7705', 'Nokia 7705 · Landscape', 320, 240, 0x01097028,
    'ff2bdd5814230aea556af04e4df1f93c2512fa90a22c21cf09095cdd4cd5d09d',
    'v.1.5.2.1 - nokia 7705 - 320x240', '278583', asset_hashes=(
        ('bio1.dat', '1551f075ccb8a4bd181d465ffeb991019bdfc8124943246eefbb61d489a337d8'),
        ('bio2.dat', 'df335e8a4ca4dc4b55393a07d706888b36c5adca17a345b773461ac85fcac8fa'),
        ('bio3d.idx', '29667fc44fc718746b73a4f57ffb787735ccb4f2af7fe81cfa6de1b6907a0cb9')))
PROFILES = (VX8350, N7705)

KNOWN_BUILDS = (
    BuildProfile('u410', 'Samsung U410', 176, 220, 0x0109004F,
                 '0780415b215c5207450c2649ade8a629b7b33bd427028ce5fc9c9788e66a05a5',
                 'v.1.1.1.1 - unknown - 176x220', '278579'),
    VX8350,
    BuildProfile('u940', 'Samsung U940', 240, 320, 0x0109AB48,
                 '68dd691dde5ece71d758d32ca1a7126435508b83c48d4fc34d6bd144eee241e0',
                 'v.1.5.1.4 - sams u940 - 240x320 - rotated - touch', '278839'),
    BuildProfile('vx10000', 'LG VX10000', 320, 240, 0x01090056,
                 '32acf7c7df3e0e89523f0b0c30a4bbd597912bc9c8597503031838690fc59718',
                 'v.1.5.1.6 - lg vx10000 - 320x240', '278581', software=False),
    N7705,
    BuildProfile('mx8700', 'LG MX8700', 240, 320, 0x0108FB17,
                 'b36a2584cca6ccedcad03425f2d9d51d4ba5619e1984b8dc5abed2ac4e113837',
                 'v.1.6.1.1 - lg mx8700 - 240x320', '278597', software=False),
    BuildProfile('u650', 'Samsung U650', 240, 320, 0x0109004E,
                 'c9a729379e23e25b63d0ebd7994045105f630c25e9727fbfbcb3e48742220f8d',
                 'v.1.6.1.3 - sams u650 - 240x320', '278596'),
    BuildProfile('c711', 'Casio C711', 240, 320, 0x01090059,
                 '108b7d70c22358a902ada876952d1902a7f078d5dd81a23b018cd1d82e4dc1d4',
                 'v.1.6.1.5 - casio c711 - 240x320', '278591'),
)


def discover_root() -> Path:
    override = os.environ.get('BIO3DPC_LIBRARY_DIR')
    if override:
        return Path(override).expanduser()
    checkout = Path(__file__).resolve().parent.parent
    candidates = (checkout / 'bioshock3d_game_files', checkout / 'bio3d_versions',
                  Path.home() / 'Downloads/bioshock3d_game_files',
                  Path.home() / 'Downloads/bio3d_versions')
    return next((path for path in candidates if path.is_dir()), candidates[0])


def relocated_local_path(value: str) -> str:
    """Follow remembered paths when this checkout's old collection was moved."""
    if not value or Path(value).expanduser().exists():
        return value
    checkout = Path(__file__).resolve().parent.parent
    try:
        relative = Path(value).expanduser().relative_to(checkout / 'bio3d_versions')
    except ValueError:
        return value
    root = checkout / 'bioshock3d_game_files'
    if relative.parts and relative.parts[0] in {p.folder for p in KNOWN_BUILDS}:
        candidate = root / 'release_builds' / relative
    else:
        candidate = root / relative
    return str(candidate) if candidate.exists() else value


def identify(path: str | Path) -> BuildProfile | None:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return next((profile for profile in KNOWN_BUILDS if profile.sha256 == digest), None)


def default_save_dir(path: str | Path) -> Path:
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    if not state.is_absolute():
        state = Path.home()/'.local/state'
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return state / 'bio3dpc' / 'saves' / digest
