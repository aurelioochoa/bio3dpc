# Original builds and preservation coverage

The original default is **LG VX8350 v1.4.1.1, 176×220 portrait**, module
278595, applet class `0x0109005B`. The second launcher edition is the genuine
**Nokia 7705 v1.5.2.1, 320×240 landscape** build, module 278583, applet class
`0x01097028`. The landscape edition executes its own original ARM binary;
the host does not rotate or stretch the portrait version.

## Why VX8350 was selected first

The local collection contains eight module builds and four separate seven-pack
data sets. Re-running `tools/verify_builds.py --inventory` established that
only `data/vx8350` matches a module's shipped `bio1.dat`, `bio2.dat` and
`bio3d.idx` byte for byte. All seven file sizes match that index. The full
matching data set, modest framebuffer and software renderer made it the
smallest dependable starting point. The former hard-coded screen dimensions
were a consequence of that initial choice, not a reason to exclude landscape.

## Collection inventory

The local collection is now `bioshock3d_game_files/`: original handset folders
are grouped under `release_builds/`, separate packs remain under `data/`,
prototypes live under `development_builds/`, and downloaded bundles under
`archives/`. Legacy flat collections remain supported.

| ID | Device / version | Native screen | Applet class | Rendering / launcher status |
| --- | --- | --- | --- | --- |
| u410 | Samsung U410 / 1.1.1.1 | 176×220 | `0x0109004F` | Software; not qualified |
| vx8350 | LG VX8350 / 1.4.1.1 | 176×220 | `0x0109005B` | Portrait launcher edition |
| u940 | Samsung U940 / 1.5.1.4 | 240×320 | `0x0109AB48` | Rotated/touch folder; not qualified |
| vx10000 | LG VX10000 / 1.5.1.6 | 320×240 | `0x01090056` | EGL/GLES-dependent build; not qualified |
| n7705 | Nokia 7705 / 1.5.2.1 | 320×240 | `0x01097028` | Landscape launcher edition |
| mx8700 | LG MX8700 / 1.6.1.1 | 240×320 | `0x0108FB17` | EGL/GLES-dependent build; not qualified |
| u650 | Samsung U650 / 1.6.1.3 | 240×320 | `0x0109004E` | Software; not qualified |
| c711 | Casio C711 / 1.6.1.5 | 240×320 | `0x01090059` | Software; not qualified |

Full module SHA-256 identities and matching starter-pack hashes are recorded
in `bio3dpc/profiles.py`. The verifier checks each class ID against its actual
MIF record, including the non-four-byte-aligned records.

The original Nokia folder contains only `bio1.dat` and `bio2.dat`. Its
`bio3.dat` through `bio7.dat` are absent. The other devices' data sets are
not substitutes: their first packs and indexes do not match this build.
The launcher states that later campaign assets are unavailable. First-scene
verification is not a full campaign qualification, including for VX8350.

`vx9400` and `vx9700` have internally consistent indexes but match none of
the eight modules. Samsung U940 is a different device from LG VX9400.
The `vx9900` index disagrees with the actual sizes of `bio3.dat` through
`bio7.dat`; it is not offered as a playable edition.

A resource-level comparison identified useful recovery candidates without
substituting them into this release. For each distinct resource name's last
record, Nokia and vx9900 share 389 of 395 `bio1.dat` payloads exactly; the six
differences are text. Their `bio2.dat` shares 98 of 100 payloads; the differences
are tutorial text, with an extra `Thumbs.db` in vx9900. The actual vx9900
`bio3.dat` and `bio6.dat` sizes match Nokia's index. Other packs still differ,
and matching sizes or shared starter resources do not prove authentic later
packs or later-level compatibility. The verified landscape profile continues
to use its original starter files, with the missing-content warning retained.

## Landscape compatibility work

A separate LG VX9900 v1.5.1.2 build1 prototype was recovered from the
[preservation archive](https://archive.org/details/bioshock3d_mobile), nested in
`development builds.zip` as `bio3d_lg9900_1.5.1.2_build1.rar`. Its ARM module and
Windows simulator DLL each ship with all seven packs. Every pack matches its
bundled index size, and parsed resources include all four released chapters.
These files are in `development_builds/lg9900-v1.5.1.2-build1/`, with hashes and
resource inventory in `verification.json`. This is a distinct prototype data set
from `data/vx9900`; runtime compatibility and campaign playability are unverified.
It is not currently offered by the launcher.

The Nokia binary needs its own class ID and framebuffer dimensions, plus an
OEM filesystem class `0x0102D575` with the IFileMgr vtable ABI. The native
loader calls its Test and OpenFile methods; returning the standard IFileMgr
implementation prevents the startup null dereference and loads its own packs.

The native engine's main structure omits 0x114 bytes of handset-specific
fields before the relevant context fields. Input function offsets also differ:

| Native function | VX8350 file offset | Nokia file offset |
| --- | --- | --- |
| Key query | `0x2C898` | `0x2CCCC` |
| Camera input pass | `0xBEF50` | `0xBF2E0` |

The input observer is enabled only for the exact known module hashes.
Equipment, inventory, camera and original game code are not patched by the
production controls. See [REA workflow](rea-workflow.md) for static evidence
and its limits. `tools/verify_builds.py --gameplay` independently exercises
the live guest and checks that source files remain unchanged.

## Saves and originals

The PC launcher writes through an overlay in
`$XDG_STATE_HOME/bio3dpc/saves/<module-sha256>/`, falling back to
`~/.local/state`. Existing source saves can be read and copied on first write;
the original data and saves are not opened for writing. Each build has an
independent save directory. Deletion and renaming apply to overlay files;
source-only files cannot be removed or renamed through the guest service.

`Boot` retains its opt-in `save_dir` argument for development clients. The
PC entry point always supplies a save overlay; direct experimental users of
`Boot` must supply it themselves when they need original-file preservation.
