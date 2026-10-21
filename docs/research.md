# BioShock 3D (Indiagames, BREW, 2010) → Linux/PC HLE port — research notes

Historical investigation notes: sections 1–6 include early planning, while
section 7 records earlier VX8350 measurements. Current portrait/landscape
coverage, REA evidence and the animated desktop launcher are documented in
[builds.md](builds.md), [rea-workflow.md](rea-workflow.md), and the README.

This document is the technical research basis for `bio3dpc`, a project that runs the
**original, unmodified ARM application** of the 2010 BREW mobile game *BioShock 3D*
inside a high-level emulation (HLE) host on Linux/PC. It is written for a developer
who has never seen BREW and covers: the game and every build we possess, the BREW/AEE
platform it was written against, the prior art, the chosen architecture, the
risks, the measured playthrough, and every source used.

Notes on evidence:

* "(measured)" = the number or string was produced by running a command in this
  session against the files listed in §8. The measurement scripts are preserved:
  `/tmp/opencode/bio3d_recon/final_check.py` (sizes, MD5s, MIF class IDs),
  `/tmp/opencode/bio3d_recon/final_check2.py` (MIF strings, `.mod` headers),
  `/tmp/opencode/bio3d_recon/dat_audit.txt` (`.dat` container parse, 28 files).
* Claims that could **not** be fully verified are explicitly flagged "unverified".
* Version-folder names contain spaces; all quoted paths use them verbatim.

---

## 1. Executive summary

**What the port is.** `bio3dpc` executes the shipped ARM machine code of the game
(`bio3d.mod`, an ARMv5-era BREW module) on the Unicorn CPU emulator [9][10] inside a
Python host process, while providing the BREW runtime the module expects — the AEE
APIs (`IShell`, `IDisplay`, `IGraphics`, `IBitmap`, `IFileMgr`, `IHeap`, …) and the
`AEEHelperFuncs` C runtime table — as *native host implementations* that bridge to
platform services (planned: SDL2 window/input, OpenGL blit, FFmpeg/QCELP audio).
Only the AEE *interfaces* are emulated; the game's own engine, file formats, game
logic, and (for the targeted build) its software rasterizer all execute as real ARM
code on the emulated CPU.

**What it is not.**

* It is **not** a reimplementation, remake, or source-port of the game. No game code
  is decompiled or rewritten; the original binary is the program being run.
* It is **not** a phone emulator. It does not emulate a Qualcomm baseband, the phone
  RTOS, the BREW apploader/installer, code-signing checks, or any MIF-based app
  management beyond the minimum needed to launch the one applet [2] (and Infuse's
  authors made the same architectural choice for BREW/Zeebo [4][5]).
* It bundles **no game data and no game code**. MIF/MOD/DAT assets are user-supplied;
  the repository contains only host code and documentation. This is both a legal
  requirement (the assets are copyrighted by their owners) and the same rule the
  sibling project `re4mepc` follows.
* The initial investigation ended at boot and service traces. The runtime now
  renders real guest frames, supports audio and PC controls, and launches native
  portrait and landscape builds. Later campaign coverage remains unqualified.

**Target build recommendation:** run the **LG VX8350, version 1.4.1.1, 176×220**
build (folder `v.1.4.1.1 - lg vx8350 - 176x220`, module `278595`, app class ID
`0x0109005B`). It is the only build in our set for which a complete, internally
consistent, byte-matching data set exists (measured; §2.5). Justification, and what
the higher-resolution builds would additionally require, are in §2.6.

---

## 2. The game and its versions

### 2.1 Identification

* **Title:** BioShock 3D (mobile). **Developer:** Studio Tridev (IG Fun).
  **Publishers:** Indiagames Ltd, Starwave, 2K Games. **US release:** Feb 9, 2010.
  First-person shooter, single player, mobile phones (BREW).
  — BioShock Wiki [14] (fetched via its MediaWiki wikitext API).
* **Platform:** Qualcomm BREW, C/C++. Developers state the 3D version "needed the
  BREW Emulator" and would not run on J2ME (the 2D version did) — this is the crux
  of why a BREW-capable runtime must exist to run it at all [14].
* **Content:** the first four levels of *BioShock* (Welcome to Rapture, Medical
  Pavilion, Neptune's Bounty, Smuggler's Hideout); later levels were developed but
  never released. After level one, levels were *downloaded individually* [14]. The
  `.mod` binaries still contain per-device download URLs — e.g. in the vx8350 build:
  `http://gcd.igstudios.in/pub/US/downloadfile.jsp?gname=bioshock3d&model=8350&cid=`
  (measured, string scan of all 8 `.mod` files; full list in §2.4).
* **Rendering:** per developer comments, the game supported both hardware
  OpenGL ES 1.0 (Common-Lite profile) and an in-house **software rasterizer** for
  phones without GPUs; hardware acceleration was used on high-end devices such as
  the "LG Voyager" [14]. The LG Voyager is the VX10000 — and, measured, the EGL/GLES
  code strings appear in exactly the VX10000 and MX8700 builds (§2.3). No background
  music; sound effects kept; audio diaries became text logs; textures built from
  tiled semi-voxel level maps (IGN, as cited by [14]).
* **Distribution era:** Verizon's BREW catalog (the MIF app metadata even carries a
  Verizon content-filter rating context in the wiki infobox: VWE, T13+). BREW handset
  catalogs tied installs to per-handset signatures, which makes sideloading hard; the
  practical record of these apps today is loose `.mif`/`.mod`/`.dat` dumps like this
  collection [2].
* **Company:** Indiagames was a Mumbai-based mobile games publisher, later part of
  UTV/Disney; "IG Fun"/igfun.com and igstudios.in appear as delivery hosts in the
  binaries (measured URLs, §2.4) [16].

### 2.2 What a "build" consists of

Each folder under `~/Downloads/bio3d_versions/` is one device build:

```
<version folder>/<model number>.mif      ← application metadata (BREW installer record)
<version folder>/<model number>/bio1.dat ← level-1 resource container
<version folder>/<model number>/bio2.dat ← level-2 resource container
<version folder>/<model number>/bio3d.idx← asset index (text)
<version folder>/<model number>/bio3d.mod← the ARM application itself (the port target)
```

The `.mif` is the app record the phone's BREW shell reads: display name, vendor
strings, icon, and the **app class ID** used to create the applet.
Measured contents of every MIF (4102–4122 bytes):

* UTF-16LE strings: `Indiagames Ltd.`, `(c) Indiagames Ltd.`, a version string
  (e.g. `V 1.4.1.1`), `BioShock 3D` (measured, `final_check2.py`).
* One 8-bit RGB PNG icon, 26×26 (`IHDR … 0x1a 0x1a 08 02`), extracted per build to
  `/tmp/opencode/bio3d_recon/mif_icons/` (24 files, `278xxx_0..2.png`).
* The app class ID, stored as a little-endian u32 at a **2-byte-aligned**
  offset (e.g. 0xff2), so a naive 4-byte-aligned scan misses it — a practical
  gotcha when re-scanning the files.

The `.mod` is the program: an ARM (`0xEA…` branch instructions at offset 0) module
with a fixed header. Measured header of the vx8350 build:

| offset | value (vx8350) | meaning (documented in `bio3dpc/modfile.py`, verified by shim disassembly) |
|---|---|---|
| 0x00 | `0xea00000e` | ARM branch — cold entry (runs relocation once) |
| 0x04 | `0xea00000f` | ARM branch — warm entry (skips relocation) |
| 0x08 | `"BREW"` (`0x57455242`) | magic |
| 0x10 | `0x40` | offset of the cold entry |
| 0x14 | `0x101` | format version 1.1 |
| 0x18 | `0x200` | text offset — `AEEMod_Load` lives here |
| 0x1c | `0x12e6c0` | text size |
| 0x20 | `0x240` | data size |
| 0x24 | `0xbfc` | BSS size (zeroed after relocation) |
| 0x28 | `0x130` | relocation-table size |
| 0x34 | `0x12e6c0` | image size |
| 0x38 | `0x8000` | suggested heap size |
| 0x18c | `0xfffffe74` | self-relative base word (= −0x18C; all 8 builds) |

The image is **position-independent**: whatever 4-aligned address the image is copied
to becomes its base. Two loader-facing consequences, both used by `bio3dpc/modfile.py`
and `boot.py`:

1. `[base−8]` must hold the helper-table **version** and `[base−4]` a pointer to the
   `AEEHelperFuncs` table; the module's shim forwards both to `[text−8]/[text−4]`.
2. Calling `base` with `(IShell*, ph, IModule**)` performs the one-time relocation and
   returns the `IModule*` — exactly what the phone's loader would call.

### 2.3 All eight builds — measured table

Every row was measured by MD5/size scanning (`final_check.py`) and the MIF/`.mod`
scans (`final_check2.py`). "CLSID" = the u32 app class ID found in the MIF at the
listed file offset; "shim" = the shim's BSS/reloc sizes at `.mod` offsets 0x24/0x28;
"EGL" = presence of `EGL_QUALCOMM_COLOR_BUFFER` + EGL error strings in the `.mod`.

| folder | MIF / size | MIF version string | app CLSID (offset) | `bio3d.mod` size / md5 | shim bss/reloc | EGL | distinguishing evidence |
|---|---|---|---|---|---|---|---|
| `v.1.1.1.1 - unknown - 176x220` | `278579.mif` / 4102 | `V 1.1.1.1` | `0x0109004F` (0xff2) | 1235136 / `5b7b9e70ead36056275bbc4850dd1720` | 0xbfc / 0x130 | – | `.mod` URL says `model=u410`; earliest version |
| **`v.1.4.1.1 - lg vx8350 - 176x220`** | `278595.mif` / 4102 | `V 1.4.1.1` | **`0x0109005B`** (0xff2) | 1243104 / `755780e7754c8a437914df78e9d1bac8` | 0xbfc / 0x130 | – | URL `model=8350`; matching data set exists (§2.5) |
| `v.1.5.1.4 - sams u940 - 240x320 - rotated - touch` | `278839.mif` / 4114 | `V 1.5.1.4` | `0x0109AB48` (0xff6) | 1246192 / `bf25a808e7b750e567f07f0f6737de8c` | 0xbfc / 0x130 | – | URL `model=u940`; rotated screen + touch (folder name) |
| `v.1.5.1.6 - lg vx10000 - 320x240` | `278581.mif` / 4122 | `V 1.5.1.6` | `0x01090056` (0xffe) | 1137056 / `ea51a6c4f9afef23edad7eeada8aed43` | 0x80c / 0x70 | ✓ 6 strings | URL `model=10000`; EGL path (LG Voyager = VX10000 [14]) |
| `v.1.5.2.1 - nokia 7705 - 320x240` | `278583.mif` / 4102 | `V 1.5.2.1` | `0x01097028` (0xff2) | 1246160 / `24384528a754d9c6461e387b43d9640d` | 0xbfc / 0x130 | – | URL `model=7705` |
| `v.1.6.1.1 - lg mx8700 - 240x320` | `278597.mif` / 4102 | `v 1.6.1.1` (lowercase v) | `0x0108FB17` (0xff2) | 1126384 / `7ed65e9210c8952aa7eba276884df002` | 0x80c / 0x70 | ✓ 6 strings | URL `dlbspack.jsp` (igfun.com, no `model=`); EGL path |
| `v.1.6.1.3 - sams u650 - 240x320` | `278596.mif` / 4102 | `V 1.6.1.3` | `0x0109004E` (0xff2) | 1237280 / `3bf4290baea51f4a857c708764a2c3e0` | 0xbfc / 0x130 | – | URL `bioshock3D8550.jsp` (igfun.com) |
| `v.1.6.1.5 - casio c711 - 240x320` | `278591.mif` / 4102 | `v 1.6.1.5` (lowercase v) | `0x01090059` (0xff2) | 1238336 / `7a1340fc8731878ba16c088fd3d5c904` | 0xbfc / 0x130 | – | URL `model=Motov750` (binary shared with Motorola V750) |

Per-build shipped data (in `<folder>/<num>/`), measured:

| build | `bio1.dat` | `bio2.dat` | `bio3d.idx` |
|---|---|---|---|
| 278579 | 3019004 / `739851d1a1f0b0c00bf388af242a7013` | 2077275 / `2c8bdf886983f674c50c08b1237a1c59` | 156 / `1fb2cc1c856a0d9093e74110c50c8664` |
| 278595 | 2989703 / `421edc9ad1f1514846640fd4b6da2e42` | 2319723 / `dfdce57703b9cde2e9b08bebc6beb782` | 156 / `b2e77af654cb9750c0aae2f9bc5f104f` |
| 278839 | 2997271 / `1a18f2584ca30d132dc27174ef72f416` | 2299514 / `4519d36e7b773d22ecb06bbc99af998c` | 156 / `c616afb4100a951315daf6049717e1ef` |
| 278581 | 3450418 / `d7027abca2713817a73511eeb1273611` | 1845598 / `964b810fa1c01e4ad3a27e0940b89705` | 156 / `3cc8fcb3b5a4178d706cd5f48189c575` |
| 278583 | 2888047 / `c38d72d688b08a6ad711eeda5f87fe0e` | 2299627 / `82718071bf23c94b83042c4737fa4ebf` | 156 / `a331e442868db4364ef70be8b1822aef` |
| 278597 | 3301583 / `6950847188c540052967742714bcf99d` | 1851922 / `446def49df509b75bb8c00b9fd918f1d` | 156 / `b97051cd7e25cd56df49bf42f338b224` |
| 278596 | 2808340 / `3e56493c184b44ff2356956d037ee95c` | 2320521 / `86652a0dcce33cc43ddb9e0b71a38daf` | 156 / `a79e8f5fc5a5b9709b74c4874fdaeabb` |
| 278591 | 2808340 / `77cc2dede3c30501196555fc40b571b4` | 2318641 / `aad54542522e332b1dbcc3274b27624b` | 156 / `50b4d2df9bff572f6311d694eb011422` |

All builds also contain the full `.mod` string evidence for the engine: `igmodel`,
`igmesh`, `igtexture`, `savedata.dat` (8 occurrences), `Engine Initialised!`,
`points_%d.data`, `Model format version = %d.%d` (verified by string scan; earlier
full dumps in `/tmp/opencode/bio3d_recon/mod_strings.txt`). The presence of
`savedata.dat` and the download URLs confirms the game used the BREW file system and
HTTP for levels/save data — both must exist as HLE services eventually.

### 2.4 Version/URL string map (measured)

`.mod` download URLs found in each build (string scan):

| build | in-binary URL |
|---|---|
| 278579 | `…downloadfile.jsp?gname=bioshock3d&model=u410&cid=` |
| 278595 | `…downloadfile.jsp?gname=bioshock3d&model=8350&cid=` |
| 278839 | `…downloadfile.jsp?gname=bioshock3d&model=u940&cid=` |
| 278581 | `…downloadfile.jsp?gname=bioshock3d&model=10000&cid=` |
| 278583 | `…downloadfile.jsp?gname=bioshock3d&model=7705&cid=` |
| 278597 | `http://www.igfun.com/pub/US/dlbspack.jsp?l=` |
| 278596 | `http://igfun.com/pub/US/bioshock3D8550.jsp?l=` |
| 278591 | `…downloadfile.jsp?gname=bioshock3d&model=Motov750&cid=` |

Two useful observations: (a) the "unknown" folder's binary identifies itself as
`model=u410`; (b) the c711 build's URL says `Motov750`, implying the same binary
shipped for at least two devices — folder names are hints, binary strings are
evidence.

### 2.5 Data sets shipped alongside the builds

`~/Downloads/bio3d_versions/data/` contains four 7-file sets
(`bio1.dat`…`bio7.dat` + `bio3d.idx`, 156 B idx). The index is plain text:

```
INDX\r\n7\r\nbio1.dat\r\n002989703\r\nbio2.dat\r\n002319723\r\n…   (9-digit zero-padded sizes)
```

Measured totals and consistency (idx-declared size vs actual size):

| set | total bytes incl. idx | declared vs actual |
|---|---|---|
| `vx8350` | 9164178 | 7/7 match |
| `vx9400` | 10283841 | 7/7 match |
| `vx9700` | 9232962 | 7/7 match |
| `vx9900` | 9039660 | **bio1, bio2 match; bio3–bio7 mismatch** |

The `vx9900` inconsistency, quantified: idx declares
906124/1078801/39805/1045932/781702 for bio3…bio7 while the actual files are
920678/1035583/39806/1037976/802878. The declared numbers for bio3–bio7 are exactly
the *vx9700* file sizes, yet the actual vx9900 files are also not byte-identical to
the vx9700 files (all seven MD5s differ; measured). The set is mixed-origin and must
be treated as untrustworthy.

**Matching to builds (measured):** only the vx8350 set pairs with one of our eight
builds — `data/vx8350/bio1.dat`, `bio2.dat` and `bio3d.idx` are byte-identical (MD5)
to the 278595 build's copies, and its bio3…bio7 are internally consistent. The other
pairings fail:

* `data/vx9400` does **not** match the `v.1.5.1.4 - sams u940` build (different
  device: LG VX9400 vs Samsung U940; bio1 3376106 vs 2997271 bytes, different MD5).
* `data/vx9700` and `data/vx9900` bio1 sizes (2916308 / 2888067) match no build's
  bio1 (sizes across builds: 3019004, 2989703, 2997271, 3450418, 2888047, 3301583,
  2808340 ×2) — their builds are not in this collection.

### 2.6 Recommendation and higher-resolution path

**Recommendation: LG VX8350 v1.4.1.1, 176×220, `278595`, CLSID `0x0109005B`.**

1. It is the only build whose data is complete *and* verified matching (§2.5):
   bio1/bio2/idx byte-identical plus a consistent bio3…bio7. Without this, any
   in-game progress past level 1–2 hits missing or corrupt assets.
2. Its screen (176×220) is the resolution the current host advertises
   (`services.py`: `SCREEN_WIDTH=176`, `SCREEN_HEIGHT=220`) and the class ID already
   hard-coded in `boot.py` — less risk of mixing build-specific assumptions.
3. It is (with 278579) the oldest bottom-end build: the software-rasterizer path and
   no EGL dependency (no EGL strings measured), so rendering does not require any
   GLES emulation to reach the first frames. (The rasterizer runs as guest ARM code;
   unverified until the first frame is captured, but the binary contains no EGL/GLES
   code paths, unlike 278581/278597.)
4. It matches the retail Verizon-era experience (the wiki's release/store context
   cites the Verizon catalog [14]).

**What higher-resolution / other builds would additionally need:**

* **Per-build app class ID.** Each MIF carries a different CLSID (table §2.3); the
  loader must pass it to `IModule::CreateInstance`. `boot.py` currently hard-codes
  `0x0109005B` with a comment saying "every build we have uses 0x0109005B" — that
  premise is *false* (measured); this is a latent bug for every non-vx8350 build.
* **A matching data set per device** (see §2.5) — currently only vx8350 qualifies.
* **Samsung U940 (278839):** the screen is *rotated* and *touch*-enabled (folder
  name; unverified in code). Requires landscape presentation and a touch input
  mapping.
* **LG VX10000 (278581) / LG MX8700 (278597):** these two build the EGL path
  (`EGL_QUALCOMM_COLOR_BUFFER`, "EGL extension for BREW 2D rendering") and the
  smaller shim variant (bss 0x80c). On these devices the game expects a working
  EGL/OpenGL ES 1.x stack; a port needs either a GLES-1.x-on-GL host layer (as
  Infuse implements for BREW [5]) or a way to force the software path. Out of scope
  until the 176×220 build runs.
* **Nokia 7705 / Samsung U650 / Casio C711:** no extra platform features observed;
  same requirements as vx8350 plus CLSID + data set.

---

## 3. BREW / AEE platform background

### 3.1 What BREW is

BREW (Binary Runtime Environment for Wireless) is a Qualcomm application runtime
for feature phones: a C/C++ layer between the phone's RTOS and applications,
providing graphics, file, network, media, and UI primitive APIs [1]. Versions per
the archived Emulation General Wiki [2]: 1.0 (2001), 2.0–2.1 (2002), 3.0–3.1 (2004),
4.0 (2007), BREW MP (2009). `BioShock 3D` is a BREW 3.x-era app (the SDK directory
used for this project's ABI reference is BREW 3.1.5 SP02). It was also the software
platform of the Zeebo console [2]. Handset catalogs distributed apps as
handset-signed packages; signatures are bound to the device, which complicates
sideloading and is part of why loose-file collections like ours circulate [2].
Known BREW/Zeebo emulation efforts are listed in [2] and §4.

### 3.2 Module lifecycle (and what the host has to fake)

The lifecycle every BREW application follows, with the SDK headers that document it
(local paths in §7):

1. **The loader maps the `.mod`** and calls its entry; the module's own shim
   relocates the image (the format is described in §2.2).
2. **`AEEMod_Load(IShell*, ph, IModule**)`** — declared in `AEEModGen.h:95` — is the
   module's exported entry. It returns the module's `IModule` instance.
3. **Helper table handshake.** `AEEStdLib.h:296-297` defines the ABI used by the
   module's C runtime:

   ```c
   #define GET_HELPER()      (*(((AEEHelperFuncs **)AEEMod_Load) - 1))
   #define GET_HELPER_VER()  (*((uint32 *)(((byte *)AEEMod_Load) - sizeof(AEEHelperFuncs *) - sizeof(uint32))))
   ```

   i.e. the two words immediately before the module's text are the helper-table
   pointer (at `text−4`) and its version (at `text−8`). The host writes
   `[base−8] = 0x00010000`, `[base−4] = table` and the shim forwards them to their
   final location (`bio3dpc/boot.py`, `modfile.py`). `AEEHelperFuncs` itself is a
   fixed-order struct of function pointers declared at `AEEStdLib.h:44-46` —
   the field order *is* the binary ABI (table in `bio3dpc/helper.py`, 117 entries,
   version `0x00010000`).
4. **`IModule::CreateInstance(IShell*, AEECLSID, void**)`** — vtable slot 2
   (`IModule = (AddRef, Release, CreateInstance, FreeResources)`, generated in
   `bio3dpc/ifaces_gen.py` from the SDK headers). Returns the applet object
   (`IApplet*`, also returned via `AEEClsCreateInstance` declared at
   `AEEModGen.h:104`).
5. **Applet events.** The shell drives the applet by calling
   `IApplet::HandleEvent(IApplet*, uint16 evt, uint32 wParam, uint32 dwParam)`
   (vtable slot 2). `EVT_APP_START` (0x0000) starts the game; `AEEAppStart` carries
   `{error, clsApp, IDisplay*, AEERect, char*}` (`AEEShell.h:37`,
   "Arguments sent on EVT_APP_START/EVT_APP_RESUME"; event semantics described in
   `AEE.h:672-683`; `bio3dpc/services.py` encodes it). Further events: stop (0x0001),
   suspend/resume, key events, etc.
6. **Flow control cooperates with the shell**: BREW is not preemptive for app code;
   the app arms one-shot timers via `IShell::SetTimer(msec, PFNTIMER, user)` and
   the shell calls back. `bio3dpc/services.py` implements this contract (one-shot
   timers, re-armable from inside a callback) and drives everything with `pump()`.

### 3.3 Interface/vtable object model

AEE objects are COM-like: a 32-bit vtable pointer is the object; each interface
begins with `AddRef`, `Release` (+ `QueryInterface` on later interfaces), then its
methods. Examples from the generated vtable tables (extracted from the SDK headers
by `tools/gen_ifaces.py`; 142 interfaces): `IApplet = (AddRef, Release, HandleEvent)`;
`IModule = (AddRef, Release, CreateInstance, FreeResources)`;
`IHeap = (AddRef, Release, Malloc, Realloc, Free, StrDup, CheckAvail, GetMemStats,
GetModuleMemStats)`. AEE applications are built from interfaces all obtained from
one root: `IShell` — the object the loader hands to `AEEMod_Load`.
`IShell::CreateInstance` is the factory for everything else (`IDisplay`, `IGraphics`,
`IHeap`, `IFileMgr`, …; see `bio3dpc/services.py`). This single-root model is what
makes HLE tractable: the host can fabricate any object as `{vtable of trap stubs}`.

### 3.4 The `AEEHelperFuncs` table

`AEEHelperFuncs` covers what a C runtime would provide: memory (`malloc`, `free`,
`realloc`, `memmove`, `memset`, string and wide-string functions), formatting (`sprintf`,
`snprintf`), math (`f_op`/`f_calc`, trig), time (`GetTimeMS`, `GetUpTimeMS`,
`GetSeconds`, Julian dates), randomness, path helpers, and diagnostics
(`dbgprintf`). The host implements these as native Python functions behind trap
slots (`bio3dpc/helper.py`); many are pure and delegate to the host (`memmove` →
guest memory copy; `GetTimeMS` → monotonic clock). The hottest trivial ones
(`strcmp`, `strlen`, `GetAppInstance`, small `memmove`/`memset`) are ARM routines
run inside Unicorn instead (`bio3dpc/native_helpers.py`, §7.5). Critically, the
table may only be extended at the end — the guest indexes it positionally.

### 3.5 MIF resources

BREW apps can embed resources, loaded through `IShell::LoadResData/LoadResString/
LoadResObject`. The game's MIF carries the icon, strings and app IDs (§2.2);
resource loading is stubbed in `services.py` to be fleshed out when a resource is
actually requested.

### 3.6 Why HLE instead of emulating a whole phone

1. **The interesting contract is documented and small.** The app touches the
   platform only through AEE interfaces and `AEEHelperFuncs` — both defined by the
   SDK headers (local copies cited in §7). A whole-phone emulation (baseband, RTOS,
   RTXC-style scheduler, apploader, signature verification) buys nothing for one
   game and has no public documentation.
2. **Precedent.** Infuse — the current public BREW/Zeebo HLE — is explicitly an
   HLE of the "BREW subsystem" [4][5], and the game's own developers described
   running the game under "the BREW Emulator" as the norm [14].
3. **Observability.** Trapping at interface boundaries gives clean, named logs of
   every platform call (`services.py` logs stubs once with counters; the trace
   doubles as the TODO list), whereas whole-system emulation gives you a boot loop
   and a disassembler.
4. **The CPU still runs real code.** Unicorn provides full ARM semantics for the
   game itself; HLE only replaces the phone.

---

## 4. Prior art

### 4.1 Infuse (Tuxality) — BREW/Zeebo HLE emulator

* "Multiplatform Qualcomm BREW / Zeebo emulator (HLE)" [4]; project page describes a
  "Qualcomm BREW subsystem reimplementation and Zeebo high-level emulator written
  from scratch … based purely on clean reverse engineering" [5].
* Uses **Dynarmic** as its ARM JIT [5]; supports macOS/Windows/Linux/Haiku; audio
  backends for MIDI, PCM/ADPCM, MP3 (PulseAudio/ALSA/waveOut/CoreAudio/Media Kit)
  [5].
* Public state: **A1 development preview, 18 May 2024** [5]. Compatibility list
  (checked this revision) covers three playable Zeebo games plus BREW titles
  (Asphalt Urban GT 1–3 3D, Kingdom Hearts V-Cast playable; Devil May Cry: Dante X
  Vergil not working). **BioShock 3D is not listed** [5].
* Binary-only repository (README/LICENSE only, 1 commit) [4]; license is custom and
  restrictive — no redistribution, no third-party hosting, no commercial use,
  altered binaries not allowed [6]. It cannot be reused as a component; it is prior
  art and a design reference only.
* Historical relevance: first PoC (Nov 2018) was a BREW *compatibility layer* that
  ran native BREW apps on Windows Mobile ARM devices "somewhat similarly to WINE",
  loading MODs against a BREW subsystem including an OpenGL ES 1.x implementation —
  evidence that the HLE-of-AEE approach is viable outside our project [5].

### 4.2 rea (morluto) — agent-driven RE tooling

`rea` [8] is a general-purpose reverse-engineering toolkit for agents (MCP server +
CLI, MIT-licensed own code, Node 22+/24+/26+). It is not BREW-specific (its bundled
skills are generic: `reverse-engineer-anything`, `rea-tool-design`, …), but it can
drive Hopper/Ghidra/IDA over native binaries — useful for ongoing analysis of
`bio3d.mod`, symbol/string mining, and building the asset-format knowledge this port
needs. It does not execute BREW; it is tooling, not an emulator.

### 4.3 Melange and the older BREW emulator landscape

The archived Emulation General Wiki BREW article [2] lists **Melange** (mid
accuracy; contains proprietary Qualcomm components) and Infuse. Both are
closed-source binaries that cannot serve as a code base; Melange in particular was
never freely distributable. No other public BREW emulator was found in this survey.

### 4.4 CPU emulation choices: Unicorn vs QEMU user-mode vs Dynarmic

| | Unicorn [9][10] | QEMU user-mode [11][12] | Dynarmic [13] |
|---|---|---|---|
| Type | CPU emulator library (QEMU-derived core) | whole-binary user-mode emulation | ARM dynamic recompiler (JIT) |
| Interface | C library with Python bindings; hooks (code/memory), per-instruction control | run an ARM Linux ELF binary | C++20 library (emulator integration) |
| Memory model | caller-owned memory maps (`mem_map_ptr` maps host memory directly) | process address space | "bring-your-own memory system" callbacks |
| Host memory access | host reads/writes guest memory **directly** (same bytes) | only via guest syscalls | via callbacks |
| Guest OS assumption | none (bare CPU) | expects a Linux userspace + syscalls | none |
| License | GPL-2.0 [10] | GPL-2.0 (QEMU) | 0BSD (per maintained fork [13]) |
| Fit for a BREW `.mod` | **good**: no OS, we control all imports; Python host; trap dispatch | poor: no Linux syscalls to run; would need a custom "OS" anyway | technically excellent JIT, but no Python bindings; host support is x86-64/AArch64 only [13] |

The `bio3dpc` architecture mirrors its sibling project `re4mepc` (a GameCube→PC HLE
port with the same host-language choice), so **Unicorn was chosen**: it is already
wired (`.venv` has Unicorn 2.1.4 with headers), it lets the host and guest share one
memory region zero-copy, and its code hooks give exact, low-overhead trap dispatch.
Dynarmic remains the obvious performance upgrade path if crash-free CPU throughput
becomes the bottleneck — at the cost of a C++/native host layer or a binding.
Tradeoff note: QEMU user-mode is inapplicable because a BREW `.mod` is not a Linux
program; **the OS-like surface has to be provided by the host regardless**, which is
the definition of HLE. Unicorn itself is "based on QEMU" [9], so the CPU core is
proven.

---

## 5. Chosen architecture

### 5.1 Overview

```
 ┌──────────────────────────── host process: Linux, Python 3 ────────────────────────────┐
 │                                                                                       │
 │  frontend (planned: SDL2 window, keyboard, audio)          services.py — AEE HLE      │
 │  ┌──────────────────────────────────┐                     ┌─────────────────────────┐ │
 │  │ present framebuffer  ◄───────────┼───── framebuffer ───│ IShell   IDisplay       │ │
 │  │ input events ────────────────┐   │      (planned)      │ IGraphics (stub today)  │ │
 │  └──────────────────────────────┼───┘                     │ IHeap    IFileMgr/IFile │ │
 │                                 ▼                         │ IBitmap/IDIB  helpers   │ │
 │  boot.py — lifecycle driver                               └───────────┬─────────────┘ │
 │  AEEMod_Load → IModule::CreateInstance(CLSID) → EVT_APP_START          │ trap slots    │
 │                                                                        │               │
 │  machine.py — Unicorn ARM32 core (ARMv5TE semantics)                   │               │
 │  ┌────────────────────────────────────────────────────────────────────┴─────────────┐ │
 │  │  ONE 64 MiB region, mmap(MAP_32BIT) + unicorn mem_map_ptr: host and guest share  │ │
 │  │  the same physical bytes (ctypes access; the future framebuffer is zero-copy)    │ │
 │  │                                                                                  │ │
 │  │  +0x00_0000  (guard / unused)                                                    │ │
 │  │  +0x10_0000  module image (bio3d.mod copied verbatim; relocates itself)          │ │
 │  │  +0x80_0000  stack top (grows down)                                              │ │
 │  │  +0x100_0000 trap trampolines: 1 MiB = 262 144 slots, each holding `bx lr`       │ │
 │  │  +0x200_0000 guest heap (up to 32 MiB; BREW MALLOC semantics)                    │ │
 │  │  +0x3FF_0000 last MiB reserved (host sentinel & slack)                           │ │
 │  └──────────────────────────────────────────────────────────────────────────────────┘ │
 └───────────────────────────────────────────────────────────────────────────────────────┘
```

Everything above was read from `bio3dpc/machine.py`, `boot.py`, `services.py`,
`helper.py`, `modfile.py` (§7).

### 5.2 The trap mechanism (how a guest `bl` becomes a Python call)

Every host-provided function (each `AEEHelperFuncs` entry, each interface method)
gets a 4-byte slot in the trap region containing the ARM instruction `bx lr`
(`_BX_LR = 1e ff 2f e1`). The guest's import tables point there. Execution flow:

```
 guest ARM code                              host (Python)
 ───────────────                             ─────────────
 MALLOC(0x40)
   bl  <trap slot for malloc>  ──────────►  UC_HOOK_CODE fires on the slot address
                                              lookup handler "malloc" → machine.alloc()
                                              write r0 = result   (AAPCS: r0-r3 / stack)
   continues at lr (the `bx lr`) ◄─────────  hook returns; slot executes `bx lr`
```

Key properties (from `machine.py`):

* Arguments follow AAPCS: r0–r3 then stack words; return values are written to
  r0/r1 (the host supports u32/u64/f64/void result kinds).
* `call()` invokes guest functions with a sentinel return address and supports
  nesting (a service handler may call back into guest code — e.g. timers, dialog
  callbacks); each nesting level gets its own stack frame region.
* Faults (invalid memory accesses, invalid instructions, service exceptions) stop
  emulation and produce a register dump with the last service call — critical for
  debugging a 1.2 MB binary of 2009-era ARM.
* An instruction tracer and a per-call counter set feed the run log.

### 5.3 Boot sequence (implemented)

1. `HelperTable.build()` creates 117 trap slots for the `AEEHelperFuncs` ABI.
2. `ModFile` validates the self-relative base word (`0x18C == −0x18C`) and copies
   the image to the load base.
3. The host writes `[base−8] = 0x00010000` (helper version), `[base−4] = table`.
4. `m.call(base, [shell, 0, ppmod])` → guest `AEEMod_Load` performs relocation once
   and returns `IModule*`; success is checked (`rc == SUCCESS`).
5. `IModule::CreateInstance` is read from slot 2 of the module vtable and called
   with the app CLSID and the shell object → `IApplet*`.
6. `EVT_APP_START` is delivered via `IApplet::HandleEvent` (vtable slot 2) with an
   `AEEAppStart` struct advertising a 176×220×16 display.
7. `BrewRuntime.pump()` runs the loop: fire due one-shot timers (calling guest
   `PFNTIMER`s), dispatch queued events, until the app quits or a timeout; every
   unhandled service call is logged once and returns `SUCCESS` (the trace doubles as
   the TODO list).

Current status (measured: `pytest` → **21 passed, 1 skipped, 2 xfailed**): modules
load, the applet is created, `EVT_APP_START` runs, timers fire, files are served.
The service surface implemented includes `IShell` (factory, device info, timers,
events, resources stubs, properties), `IDisplay`, `IHeap`, `IFileMgr`/`IFile`
(with BREW path mapping: `bio1.dat` / `fs:/~/bio1.dat` / `fs:/shared/…` resolve onto
the user's data directory), `IBitmap`/`IDIB` (real pixel buffers in guest memory,
draw ops), and an `IGraphics` stub (all methods succeed, nothing draws). `ISoundPlayer`,
`IMedia*`, `ISocket`, `IVoice*` etc. are declared but **not yet implemented**
(`services.py`), and `boot.py` reaches the render loop only as far as the game's
IGraphics/IDisplay usage allows today.

### 5.4 Data flow once rendering/audio are implemented (planned)

* The game (target build) rasterizes into its own off-screen bitmap / the
  `IDisplay` device bitmap, which lives in the shared guest region. The host then
  presents it (SDL2 texture upload or direct blit) — no format conversion, no copy
  required thanks to `mem_map_ptr`. `IDIB` lock/unlock semantics are already
  modeled in `services.py`.
* Input maps keypad codes (the game documents a numeric-keypad layout: 2/4/6/8 move,
  5 attack, softkeys reload/health [14]) onto host keys.
* Audio: `.qcp` assets are RIFF/QLCM "Qcelp" (Qualcomm PureVoice, 13K) — the host
  FFmpeg on this machine reports a QCELP decoder (measured: `ffmpeg -codecs` lists
  `D.AIL. qcelp QCELP / PureVoice`); `.mid` files are standard MIDI and can be
  routed to a synth or ignored (no in-game music existed [14]). Neither path is
  implemented yet.
* Networking: the original download URLs are dead services; the eventual plan is to
  stub `ISocket`/media-download paths to use local files from the data set.

### 5.5 Where the format knowledge sits

The `.dat` containers and their blobs were fully reverse-engineered for tooling and
test fixtures (not required for the game itself, which parses them in guest code):

* 12-byte header `[u32 0x00040001][u32 entry_count][u32 prio_count]`, then per
  record `[u32 payload_size][u32 name_len][name][u32 data_offset][u32 index]`, with
  a 2-byte separator before each record (usually `0x0000`; one record per file
  carries `0x0001`; semantics unresolved), the **last record omitting its trailing
  index + separator** — a naive walk overshoots by exactly 6 bytes per file.
* Blobs: `[u16 0x0008][u32 payload_size][u16 typecode]` + payload; typecode 1=BMP,
  4=PNG, 3=everything else; blob chain verified contiguous for 27/28 files (one
  unknown break point; see the caveat in §6).
* Payload magics (verified by hexdump): `.qcp` = RIFF/QLCM `Qcelp`; `.mid` = MThd;
  `.m3d` = `02 00 02 00`; `.a3d` = `\x03a3d`; `.ani` = `\x03ani`; `.piv` =
  `\x05pivot`; `.fnt`/`.lvl`/`.as`/`.pos`/`.cam`/`.txt` are text formats
  (e.g. `.txt` starts `BioShock 3D\nV 1…` with a literal backslash-n).
* BMPs are 8-bit indexed 256×256 (`bfOffBits=578`); PNGs have real `\x89PNG` magic.
* vx8350/bio1.dat: 354 entries — png 2, bmp 34, a3d 88, m3d 75, qcp 112, lvl 6,
  mid 4, fnt 5, txt 15, ani 11, piv 2 (per-file histograms for all 28 `.dat` files
  in `/tmp/opencode/bio3d_recon/dat_audit.txt`).

---

## 6. Risks and open questions

1. **App class ID hard-coded for vx8350.** `boot.py:DEFAULT_APP_CLSID = 0x0109005B`
   plus the comment "Every build we have uses 0x0109005B". Measurement shows each
   build has a different CLSID (§2.3). The loader must take it from the MIF (or a
   CLI flag) before any other build can boot.
2. **Only one complete data set.** vx8350 is the only build paired with verified
   matching data (§2.5). vx9900's set is internally inconsistent; vx9400's is a
   different device; vx9700/vx9900 have no build here. *Unverified:* whether the
   game strictly needs bio3…bio7 at start or only when a level loads (the wiki says
   levels 2+ were downloads [14]).
3. **Unicorn throughput.** A 2009 software rasterizer at 176×220 inside Python
   build-around traps may not reach 100% speed on an underpowered host. Mitigations:
   frame-skip at the HLE boundary, moving hot helper stubs (e.g. `memmove`/`memset`
   bulk copies) to native fast paths, or swapping the CPU core for Dynarmic [13]. No
   performance measurements exist yet.
4. **Rendering artifacts risk.** Until the game's first frame is produced and
   compared, how much of its drawing goes through `IGraphics` vs its own rasterizer
   into an `IBitmap` is *unverified*; the port plan needs at least one of those
   paths fully correct (IDIB lock/invalidate semantics are subtle).
5. **`IGraphics` stub returns SUCCESS without drawing.** Any code that inspects
   the result of a draw call could take different branches than on hardware;
   expect to replace the stub with a real bitmap-backed implementation before the
   game is playable.
6. **Audio decisions pending:** QCELP decode path (FFmpeg capable, measured) and
   MIDI handling; `ISoundPlayer`/`IMedia*` are unimplemented. Also `IShell::Beep`,
   vibration etc. are stubs. *Unverified:* QCELP playback fidelity through FFmpeg.
7. **Missing services that the full game will hit:** network (`ISocket`,
   `IShell::LoadResDataEx` for downloads), `IShell::GetPrefs` (returns `EFAILED`
   today), dialogs/prompts, media player, clipboard. Each returns a sane default,
   but gameplay may depend on some (e.g. saving via `savedata.dat` — write path
   exists via IFile, *unverified* that the game's save/load cycle works).
8. **`.dat` container caveats:** the alternating 2-byte pad's value (`0x0000` vs one
   `0x0001` record per file) and the single blob-chain break per file remain
   unexplained. The runtime does not need the container format, but tooling and
   golden fixtures do; the parser currently special-cases the last-record rule.
9. **Legal.** The game's assets and binaries are copyrighted; the repo must not
   bundle them, and derived art/audio must not be committed. Unicorn is GPL-2.0
   [10] — if `bio3dpc` is ever *distributed* as a combined work, that license
   propagates; decide the distribution posture early.
10. **Unverified items list** (carried forward): (a) that the "unknown" 1.1.1.1
    build is for the device the URL calls `u410`; (b) that the MX8700 build shares
    the C711 binary lineage (only the URL string is evidence); (c) that the game's
    low-end path never calls EGL (absence of EGL strings is strong but indirect);
    (d) the exact hardware device models behind `vx9700`/`vx9900` sets; (e) whether
    any BIOS-like per-device init beyond `IShell` is required (none observed so
    far); (f) ~~the time needed for the game to boot to interactive gameplay under
    HLE~~ — measured, see §7.

Items 3, 4, 6 and 10(f) above were open questions when this research was written;
§7 records what running the build showed.

---

## 7. Playthrough observations (measured)

The vx8350 build runs to interactive gameplay under the port; this section records
what the port's session logs and screenshots showed. Times are wall-clock from
`EVT_APP_START` on a host AMD/Intel x86-64 running the tkinter frontend (100–200
guest ticks/s through menus and cutscenes; the opening gameplay scene runs
18.6–19.3 ticks/s — 37–38 presented frames/s — and is bound by the emulated CPU;
§7.5 has the controlled A/B numbers).

### 7.1 Screen flow

| screen (in order) | note |
| --- | --- |
| splash cards (2K, igfun, ICE 3D) | full-screen bitmaps; then skyline + `ENABLE SOUND?` on the first boot (the answer is saved) |
| BioShock logo, `PRESS OK` | any `OK`/`SELECT` |
| main menu | `NEW GAME / OPTIONS / ABOUT / CREDITS / EXIT`, plus `CONTINUE` once a save exists |
| `NEW GAME` → `WELCOME TO RAPTURE` loading card | the slowest phase: the level is unpacked and a full-screen bitmap is blitted every frame (§7.5) |
| crash cutscene, subtitles, `SKIP [RSK]` | cabin scenes, then underwater; about six `OK`s separate the stills |
| `PRESS UP TO SWIM UP` | wants `AVK_UP` (hold it until the picture changes) |
| `PRESS OK TO SWIM TO LIGHTHOUSE` | `OK` |
| `PRESS OK TO ENTER BATHYSPHERE` | `OK`, then the level loads for real |
| first-person gameplay, HUD (health + EVE) | `PRESS (2) TO MOVE AHEAD (8) TO MOVE BACK` |

Wall-clock lengths depend on host speed — the host-side work of §7.5 cut this
intro's wall time by ≈2.75×.

Between cutscene cards the screen can be nearly black (only `SKIP [RSK]` text):
those are the game's own fade states, not a rendering failure.

### 7.2 Controls verified against the guest

The original mappings confused several distinct mobile actions. `7`/`9`
strafe, `4`/`6` turn, `0` interacts, `5` attacks, `SOFT1` reloads/refills EVE,
and `SOFT2` uses first aid. `1` cycles plasmids, `3` cycles weapons, and `LEFT`
cycles available ammunition types. `SELECT` confirms prompts; `CLR` pauses.
In cutscenes `SOFT2` skips. The applet handles `EVT_KEY` (0x100) and
`EVT_KEY_RELEASE` (0x102), ignoring `EVT_KEY_PRESS`.

`bio3dpc/controls.py` guards these read-only offsets with the VX8350 image's
SHA-256. `[applet+0x20]` is the game object. Gameplay has game fields
`byte +0x1b8 == 2`, `+0x1c0 == 0`, and `+0x210 == 0`;
the last field identifies the pause overlay even while gameplay objects remain
active; `[game+0x1ec]` is the level, whose `+0x20`
points to the player. Player `+0x230` is equipment mode (1 weapon, 2 plasmid),
`+0x21c`/`+0x220` are current slots, `+0x1f8` contains five weapon pointers,
and `+0x20c` contains three plasmid pointers. Weapon availability is byte
`+0x5b`; plasmid availability is byte `+0x56`. Selection sends native `3`/`1`
taps, waiting while native interactions or equip animations block switching. Equipment selection does not write acquisition state. Unknown images fall back to native keypad mappings.

PC bindings now use A/D to strafe, E to interact, LMB to attack, R/F to
reload/heal, RMB to toggle equipment mode, 1–5/F1–F3 to select owned slots,
and the wheel to select adjacent owned slots in either direction. WASD and
E navigate/confirm outside gameplay. Raw phone keys remain on the numeric
keypad. The mobile game has no jump/crouch/zoom or PC quicksave actions.

The frontend tracks guest-key ownership across physical keys, mouse buttons
and mouse look. X11 repeat release/press pairs are coalesced at idle; releases
use physical keycodes so changing Shift while holding a key cannot leave it
stuck. Tab repeats do not toggle capture repeatedly. Focus loss clears keys,
buttons, pending releases and camera budgets, including with mouse look off.
Capture clicks are suppressed; clicks during a pointer warp are not discarded.

The verified builds now consume mouse travel in one native camera rotation per
host slice, including fractional steps, rather than holding phone look keys
across later guest updates. The previous 32-step budget could continue turning
for over a second after a flick. `GameControls.rotate_view` calls camera vtable
slots `+0xc` (yaw) and `+0x10` (pitch), synchronizing `player+0x278` with yaw.
The camera angle and player heading have opposite signs: a right turn passes
`+0x140000` to the camera and subtracts `0xa0000` from the heading. Using the
same sign for both mirrors the view and leaves movement facing another direction.
Pitch is clamped to ±25 degrees using the native pitch getter (`0x2d27c` /
`0x2d6b0`) and respects `player+0x3d8`. This preserves the camera's own matrix
recalculation and avoids editing executable instructions or inventory.

REA/Ghidra analysis on 2026-10-17 confirmed the portrait rotation routines
`0xde84c` and `0x63f58`, the getter, and the menu factory `0x51044`.
Session dossiers were checked against Capstone and live guest calls; the
ephemeral REA workspace was discarded when the host restarted. Unknown images
retain the keypad/timer fallback and never use these guest structures.

Menus use `[game+0x1c4+build_delta]`, whose `+0x20` is the active native widget.
A nonzero manager `+4` denotes a transition and disables targeting. Main-menu
kind 4 uses halfword `widget+0xa`, with Continue/Load availability at `+0x10` /
`+0x14`; row positions follow the native renderer and device dimensions.
Hover writes only native UI selection fields; clicking sends a single SELECT
tap, with blank row areas ignored. Sound/quit/save/overwrite dialogs and Options
have their own guarded selection layouts. Wheel input navigates menus or adjusts
Volume, and right click sends CLR. The in-level pause overlay is independent: `[level+0x18]` holds its UI state,
with mode byte `+4` and selected halfword `+6`. The native full-menu predicate
(`0x47748` / `0x479cc`) preserves Resume-only scripted scenes. It takes precedence over the
shell manager while `game+0x210` is set. Menus never capture or hide the pointer.
Field `game+0x1bc` is the sound-enabled flag: requiring it to be 1 incorrectly
disabled gameplay routing for muted sessions. It is no longer a state guard.

Pending input is dispatched before timers. Short down/up pairs remain pressed
until the guest queries that specific key, with an eight-timer bound for unused
phone keys. Menu taps stay active until the target menu changes, bounded by eight guest
timers; a preliminary poll cannot release them before the UI handles the click. Deferring a release never blocks unrelated movement or aiming.
A real center-crossing motion counts as travel; synthetic warp motion does not.
Untimed synthetic center events wait for the server's warp acknowledgement,
with the stale-event filter and resynchronization timeout handling the stream.

`tools/input_check.py` copies game data/saves into a temporary directory,
reaches gameplay, then drives real Tk events. It measures the real player
position and yaw counter (`player+0x278`) plus the camera's pitch direction
(`[[player+0x2b8]+0xbc]`). This avoids mistaking ambient animation for a
successful movement test. `tools/mouse_check.py` separately checks pointer
transport, and unit tests cover ownership, repeat, focus, short taps, context,
selection and multiple guest updates per pump. Equipment integration checks
initialize later-level equipment with native constructors in the disposable
guest and provision acquired flags to test additional slots;
production input never modifies acquisition flags.
`tests/test_camera_gameplay.py` also compares the actual camera basis from mouse
turns against native left/right input in both builds, and verifies that portrait
walking in all four directions preserves mouse aim and player heading.

Longer walking exposed native pitch leveling in player function `0xa4bbc`
(`0xa4eb4` on Nokia), which aligns pitch immediately or interpolates through
`0xb060` (`0xb0e8` on Nokia). With mouse look enabled, a hash-guarded hook
bypasses this leveling dispatcher and two walking callers that restore a cached
camera direction. The camera movement/collision update still executes. The hooks
leave scripted pitch controllers (`player+0x3d8`) and mouse-disabled sessions
on their original paths. Regression coverage includes fractional pitch, large
yaw turns, longer forward/backward holds, and restoring handset behavior with
mouse look disabled. The host default uses 52/64 pixels per yaw/pitch step,
one quarter of the previous mouse speed; launcher Controls exposes a slider
and precise number field for the persistent sensitivity multiplier.

### 7.3 Rendering

* The whole UI (menus, subtitles, HUD numbers) is drawn from sprite-sheet PNGs
  through `IImage`: `IPARM_SIZE` is the *cell size*, `IPARM_OFFSET` the *cell
  position inside the sheet*, and `IPARM_SCALE` the destination size. Treating the
  offset as a destination offset — the intuitive reading — made every menu
  invisible while still "drawing" pixels, which is exactly the kind of failure the
  port's screenshot tooling caught.
* The 3D view goes through the game's own rasterizer into the device `IBitmap`
  (`IDIB` field layout); `IGraphics` is barely used, so item 5 of §6 is moot for
  this build. `FillRect` dominates the frame cost, so the port implements it as
  row-span writes (1.8 fps → ≈ 150 fps at the title screen).
* PNG blits are memoised per image, ROP, depth and source rect: the per-pixel
  RGBA → RGB565 conversion runs once and a 1:1 `ROP_COPY` of a full-screen
  image is then a single `memmove` into the destination bitmap (before that it
  converted every pixel on every blit and wrote the destination pixel by
  pixel).  Measured on this host: a 176×220 full-screen COPY went from
  120.4 ms to 0.087 ms, i.e. the loading cards no longer cost ~120 ms per
  drawn frame.  `tests/test_image_blit.py` pins the transparent/clipped
  semantics and a 400-case randomised run against the pre-change
  implementation produced byte-identical bitmaps.
* Font glyphs are 10×10 cells in one atlas; early-startup text lands on-screen at
  x 54–122, y 110–140, but later menu draws use coordinates beyond the 176×220
  device (x up to 323, y up to 250), so the port's blit clips them away. Whether
  the game intends a larger virtual canvas (and which origin/scale it expects) is
  still unknown; the screens that matter render inside the device rect.

### 7.4 Audio

* Voices and sound effects arrive as raw buffers whose first bytes are `RIFF` with
  form type `QLCM` — QCELP 13K ("PureVoice") — and decode cleanly through FFmpeg
  (a 25 359-byte buffer → 14.38 s, a 12 129-byte one → 6.82 s), then play through
  PulseAudio.
* Music arrives as Standard MIDI File (format 1, 7 tracks, 46081 ticks, 960
  ticks/quarter, tempo 461538 µs/quarter = 130 bpm → 22.15 s). FFmpeg cannot read
  MIDI, so the port parses the tempo map itself for the *duration* — that is what
  `ISoundPlayer` needs to deliver `AEE_SOUNDPLAYER_DONE`, and the game re-`Play`s
  the logo tune when it arrives, which is the observable proof the timing is right.
* The game's `ISoundPlayer` usage: `SetInfo(input=2, buffer, size)` → `Play`, and
  the same player instance is reused for the next source; `Stop` is called
  liberally, sometimes when nothing plays (those calls are harmless but frequent —
  expect dozens per second around scene changes).
* Rendering MIDI to audio needs a SoundFont (`fluidsynth` + a `.sf2`); without one
  every note is silent but durations still reach the guest. A 5.7 MB GM bank
  (TimGM6mb) renders the whole tune in ≈ 1 s, so rendering it once per unique MIDI
  on first play is cheap enough to be synchronous.

### 7.5 Throughput (measured)

Host time sits in two layers: the Python HLE (traps, blits, timers) and Unicorn
executing the game's own ARM code.  Both were measured.

**End-to-end, same scripted session.**  Boot → CONTINUE → loading card → intro
cut scene, 45 s window; both trees read a byte-identical *copy* of the game
data directory, because the game rewrites `savedata.dat` between sessions and a
different save makes the two runs incomparable (an earlier comparison against
a stale save swing by 10× on its own).  Two rounds each:

| tree | guest ticks/s | presented frames/s |
| --- | --- | --- |
| pre-optimisation hot paths | 51.2 / 53.3 | 102.3 / 106.6 |
| current | 143.0 / 144.0 | 286.0 / 288.1 |

≈ 2.75× more guest ticks — and frames presented — per wall second.  The game is
frame-locked by its own 2 ms one-shot timer and never reads a clock, so more
ticks per second *is* more game time per second: nothing in the guest is
retimed or sped up, the host simply stops being the bottleneck.

**What the host was spending time on.**  A 176×220 full-screen `ROP_COPY` blit
went from 120.4 ms to 0.087 ms (native pixels memoised per image/rect, then one
`memmove` per blit — the loading cards blit the whole screen every frame), the
RGB565 → RGB888 presentation conversion from 2.86 ms to 0.31 ms per frame
(byte-plane `bytes.translate`), guest `memmove`/`memset` helper calls hand
whole regions to libc instead of copying through Python objects, scalar
reads/writes go through the shared mmap with `struct` instead of `uc.mem_*`
(~1 µs per call), and the tkinter frontend presents a frame in ≈2.5 ms as
binary PPM into `tk.PhotoImage` instead of 4.18 ms as PNG + base64.

**Gameplay is guest-bound.**  At the first-person view (opening splicer scene,
HUD up) the port runs at 18.6–19.3 ticks/s (37–38 presented frames/s).  A 20 s
cProfile window on the current tree attributes ≈78 % of wall time to `emu_start`
with no Python frame on the stack — Unicorn executing the game's own software
rasterizer — ≈18.5 % to traps and host callbacks (≈860 guest→host calls per
tick, 16.4k/s; memmove/memset/malloc/string helpers dominate the handler time),
and ≈3.7 % to the pump sleeping until the next timer.  Unicorn's own ceiling on
this host, measured with synthetic loops, is ≈61 MIPS for register-only code
and ≈26 MIPS for store-heavy code; the rasterizer writes framebuffer pixels, so
gameplay speed is bounded by emulated-CPU throughput, not by the HLE.  Item 3
of §6 (weaker hosts) applies.

**Helpers inside the emulator.**  A trap round trip costs 2.3 µs for an empty
handler and 6–10 µs for `strcmp`, `strlen`, `memmove` or `memset`.  Loading a
level makes ~225k `strcmp` calls (most differ at the first byte) and ~110k
one-byte `memmove` calls; first-person play makes ~5k `GetAppInstance` and ~5k
small `memmove`/`memset` calls per second.  `native_helpers.py` runs those
five as ARM code inside Unicorn (0.03–1.5 µs per call); copies over 512 bytes,
or over 128 bytes byte by byte, still go to the host's `memmove`/`memset`.  A
deterministic replay (fake clock, keys at fixed ticks, every frame hashed)
gives identical frames with and without them: 6000 VX8350 ticks, boot to
first-person play, and 2500 Nokia ticks.  Over the first 3000 VX8350 ticks
traps fall from 1.42M to 0.43M and wall time from 80.3/80.2 s to 70.4/73.6 s,
including the replay's own per-frame hashing.

### 7.6 Persistence (measured)

The game keeps its state through `IFile` in its data directory, so the HLE write
path gets exercised without any save UI: after a few minutes of play a 105 000-byte
`savedata.dat` appeared (leading ASCII `CONTINUE`, 1.5 % non-zero, float pairs near
the end). Booting again with that file present skips the `ENABLE SOUND?` question
(the answer is remembered) and the main menu shows `CONTINUE` above `NEW GAME`
instead of five entries — i.e. read-back works too. Which of the file's 105 000
bytes the game actually validates is unverified, but the round trip is real.

---

## 8. References

Web (all fetched in this research pass unless noted):

1. Wikipedia — Binary Runtime Environment for Wireless.
   https://en.wikipedia.org/wiki/Binary_Runtime_Environment_for_Wireless
2. Emulation General Wiki — BREW (archived 2023-11-28; BREW versions table,
   MIF/MOD, Melange/Infuse, signing constraints).
   http://web.archive.org/web/20231128173512/https://emulation.gametechwiki.com/index.php/BREW
3. LPC Wiki — Qualcomm BREW. https://lpcwiki.miraheze.org/wiki/Qualcomm_BREW
4. Tuxality/Infuse (GitHub; HLE BREW/Zeebo emulator, binary-only repo).
   https://github.com/Tuxality/Infuse
5. Infuse project page (history, Dynarmic JIT, audio backends, license summary,
   compatibility lists incl. BREW titles). https://tuxality.net/projects/infuse_zeebo_emulator
6. Infuse license (raw text).
   https://raw.githubusercontent.com/Tuxality/Infuse/master/LICENSE
7. Emulation General Wiki — Infuse. https://emulation.gametechwiki.com/index.php/Infuse
8. morluto/rea — agent-driven reverse-engineering toolkit (MIT own code).
   https://github.com/morluto/rea
9. Unicorn Engine. https://www.unicorn-engine.org/
10. Unicorn Engine (GitHub; GPL-2.0). https://github.com/unicorn-engine/unicorn
11. QEMU documentation — user-mode emulation. https://www.qemu.org/docs/master/user/main.html
12. Wikipedia — QEMU. https://en.wikipedia.org/wiki/QEMU
13. Dynarmic (maintained Azahar fork; 0BSD; ARM JIT; guest v3–v8; host x86-64/AArch64;
    lists Unicorn and QEMU as alternatives). https://github.com/azahar-emu/dynarmic
    — original `merryhime/dynarmic` and `MerryMage/dynarmic` URLs returned 404 during
    this research; the fork's README carries the project description and feature list.
14. BioShock Wiki — "BioShock 3D (Mobile Game)" (article wikitext, incl. developer
    quotes sourced from Fitherwinkle's YouTube video comments and from IGN).
    https://bioshock.fandom.com/wiki/BioShock_3D_(Mobile_Game) ·
    wikitext API: https://bioshock.fandom.com/api.php?action=parse&page=BioShock%203D%20(Mobile%20Game)&prop=wikitext&format=json
15. IGN — "2K Wanted BioShock on Phones…" (original source for texture/voxel detail;
    cited by [14]; direct fetch returned HTTP 403 during this research, so the
    facts used here were read from [14]).
    https://www.ign.com/articles/bioshock-mobile-game-2k-tridev-development
16. Wikipedia — UTV Indiagames. https://en.wikipedia.org/wiki/UTV_Indiagames
17. Wikipedia — QCELP (Qualcomm code-excited linear prediction; PureVoice).
    https://en.wikipedia.org/wiki/QCELP

Local (this machine):

18. Game builds (8) and data sets (4), sizes/MD5s as in §2:
    `~/Downloads/bio3d_versions/` — e.g. target
    `v.1.4.1.1 - lg vx8350 - 176x220/278595.mif` and `…/278595/bio3d.mod`,
    `…/278595/bio{1,2}.dat`, `…/278595/bio3d.idx`; data set `data/vx8350/`.
19. BREW SDK 3.1.5 SP02 headers (ABI citations):
    `/tmp/opencode/brew_sdk/BREW315/BREW 3.1.5 SP02/sdk/inc/` —
    `AEEStdLib.h` (lines 44–46 `AEEHelperFuncs`; 296–297 `GET_HELPER*`),
    `AEEModGen.h` (line 95 `AEEMod_Load`; 104 `AEEClsCreateInstance`; 107
    `AEEStaticMod_New`), `AEEAppGen.h` (module defs), `AEE.h` (672–683 app events),
    `AEEShell.h` (line 37 EVT_APP_START args; device-info fields), `AEEEvent.h`,
    `AEEIDIB.h`.
20. Measurement artifacts: `/tmp/opencode/bio3d_recon/final_check.py`,
    `final_check2.py`, `dat_audit.txt` (28-file `.dat` parse), `audit.txt`
    (sizes/MD5s; its `.dat` section predates the corrected parser — do not use that
    section), `mod_strings.txt` (string dump), `mif_icons/` (24 extracted PNGs).
21. Port sources (read in full for §5): `~/Repos/bio3dpc/bio3dpc/` —
    `machine.py` (shared 64 MiB region, traps, `call`), `boot.py` (lifecycle,
    `DEFAULT_APP_CLSID`), `services.py` (AEE HLE objects, timers, `pump`),
    `helper.py` (AEEHelperFuncs table), `modfile.py` (`.mod` format),
    `ifaces_gen.py` (142 interface vtables), tools in `tools/gen_ifaces.py`;
    tests: `tests/test_boot_smoke.py`, `test_helper.py`, `test_machine.py`,
    `test_modfile.py`, `test_services_static.py` (21 passed / 1 skipped / 2 xfailed
    at the time of writing).
22. Sibling project whose architecture this port mirrors: `~/Repos/re4mepc/`
    (README and host conventions).
