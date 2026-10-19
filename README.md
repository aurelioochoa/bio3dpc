# BioShock 3D PC Port

**English** · [Español](README.es.md)

![BioShock 3D — BREW PC PORT, with a Bathysphere overlooking Rapture](docs/media/banner.png)

Preserve and play **BioShock 3D**, the 2010 BREW mobile game, on a Linux PC. `bio3dpc` executes the original ARM code through Unicorn while Python supplies the BREW runtime, desktop controls, audio and a tkinter window.

**Bring your own game:** original BREW modules and data are not included or downloaded. The **LG VX8350 portrait** and **Nokia 7705 landscape** editions have separate profiles. Both reach gameplay; full campaign completion has not been verified, and the Nokia collection lacks later campaign packs.

[Gameplay](#gameplay) · [Quick start](#quick-start) · [Game files](#game-files) · [Controls](#controls) · [Development](#development)

## Gameplay

Actual framebuffer recordings, enlarged 2× with nearest-neighbor scaling. These silent loops show the game running, separately from generated launcher artwork.

| LG VX8350 · 176×220 portrait | Nokia 7705 · 320×240 landscape |
| --- | --- |
| ![Portrait gameplay with wrench and HUD](docs/media/gameplay-vx8350.gif) | ![Landscape opening scene with HUD](docs/media/gameplay-n7705.gif) |
| v1.4.1.1 · seven matching campaign packs required | v1.5.2.1 · only two campaign packs available |

See [build coverage](docs/builds.md) for the exact limits of each edition.

## Requirements

- **Linux x86_64**, Python 3 with **tkinter**, and GNU Make. Development has used Python 3.14.
- Python dependencies installed through `requirements.txt`, including Unicorn and Capstone.
- **FFmpeg** and **`paplay`** on `PATH` for sound effects and voices.
- **FluidSynth** and a **SoundFont** for MIDI music. Without a SoundFont, music remains silent but keeps its timing.

Place a `.sf2` file in `~/.local/share/soundfonts/` or set `BIO3DPC_SOUNDFONT`. Missing audio tools do not prevent silent play. See [audio details](docs/advanced-usage.md#midi).

## Quick start

Install Python, tkinter and Make, then:

```sh
git clone https://github.com/aurelioochoa/bio3dpc.git
cd bio3dpc
make venv
./bio3dpc.sh
```

Choose your edition, open **Installation**, select your matching BREW module and data folder, and choose **Verify files**. After successful verification, use **Save and return → Play**.

For an applications-menu entry and the `~/.local/bin/bio3dpc` command:

```sh
make install-desktop
```

Keep the checkout in place. `make uninstall-desktop` removes the entry and installed command while preserving preferences, saves and original files.

## Game files

Supply extracted **BioShock 3D BREW game files**, keeping their original names:

| Edition | Module and matching data |
| --- | --- |
| LG VX8350 · v1.4.1.1 | `bio3d.mod`, `bio3d.idx`, and `bio1.dat` through `bio7.dat` |
| Nokia 7705 · v1.5.2.1 | Matching `bio3d.mod`, `bio3d.idx`, `bio1.dat` and `bio2.dat`; later campaign packs are missing |

In **Installation**, select **Original BREW module (.mod)** and the **Matching game data folder (.dat + .idx)**. Choose the folder containing the actual data files, rather than its parent. Verification checks module and starter asset hashes and required pack names; it does not establish full campaign coverage. Files from other handset builds are not substitutes.

**Files are used in place:** the launcher remembers their paths without copying or modifying them. Keep the source folder or external drive available while playing. APK, IPA, JAR and console ROM formats are unsupported. See [build identities and coverage](docs/builds.md) and [detailed installation guidance](docs/advanced-usage.md#installation-bring-your-own-game-files).

## Launcher

![Bathysphere launcher with edition selection and controls](docs/launcher-preview.png)

The Bathysphere launcher provides edition selection, installation verification, a window-scale slider, an audio switch and saved-game access. Its generated underwater artwork animates when FFmpeg is available; animation pauses during play and can be disabled in Settings.

Open **Controls** or **Settings → Controls** to rebind keyboard actions, enable mouse look, set sensitivity from **0.1× to 5.0×**, or invert vertical movement. Click a binding and press a new key; Backspace clears it and Escape cancels capture. **Reset controls** restores defaults; **Save and return** saves changes for future launcher and CLI sessions.

Tab navigates launcher controls, arrows adjust sliders and switches, and Enter or Space activates buttons. Narrow windows stack artwork and controls with scrolling. See [control settings](docs/advanced-usage.md#control-settings) and [launcher artwork and animation](docs/launcher-art.md).

## Controls

| Input | Action |
| --- | --- |
| W / S | Forward / backward; up / down in menus |
| A / D | Strafe left / right; left / right in menus |
| Mouse movement | Turn and look up/down |
| Left mouse | Fire weapon/plasmid; confirm in menus |
| Right mouse | Switch weapon/plasmid; back in menus |
| E | Use / pick up; confirm prompts |
| R / F | Reload or refill EVE / use first aid |
| 1–5 | Select an owned weapon |
| F1–F3 in gameplay | Select an owned plasmid |
| Mouse wheel | Cycle owned equipment in the current mode |
| Q / middle mouse | Cycle available ammunition |
| Enter / Space / keypad Enter | Phone OK / confirm |
| Escape / Backspace | Pause / back |
| Tab | Release / capture pointer |
| Numeric keypad 0–9 | Original phone number keys |
| F1–F4 in menus | Phone soft keys; F2 skips cutscenes |
| F10 / F11 | Phone soft keys 1 / 2; F11 also skips cutscenes |
| F5–F7 | Phone function keys 1–3 |
| Arrows | Phone directional pad; up/down also aim during gameplay |
| Home / End, `*` / `#` | Phone SEND / END, star / pound |

Equipment must be acquired before selection. The top number row selects equipment; use the numeric keypad for phone-number hints and hacking. Space sends phone OK, which the original help also labels as crouch. This mobile game has no separate bindings for jump, zoom, radial selection or quicksave/quickload.

Mouse look is enabled by default. Tab releases the captured pointer; clicking recaptures it without firing. Focus loss releases inputs. Menus support hover, click, right-click back and wheel navigation. Command-line overrides include `--no-mouse`, `--mouse-sens 2.0` and `--invert-y`.

For full binding rules, camera behavior and introductory phone prompts, see [the detailed controls reference](docs/advanced-usage.md#playing).

## Saves and settings

| Content | Default location |
| --- | --- |
| Original game files | Your selected module and data paths; kept read-only |
| Per-build save overlay | `~/.local/state/bio3dpc/saves/<module-sha256>/` |
| Launcher and control settings | `~/.config/bio3dpc/launcher.json` |
| Auto-detected SoundFonts | `~/.local/share/soundfonts/*.sf2` |
| Development captures and logs | `/tmp/opencode/bio3dpc` (`OUT` override) |

`XDG_STATE_HOME` and `XDG_CONFIG_HOME` override the corresponding save and settings bases. Portrait and landscape saves remain separate. The game writes `savedata.dat` to its overlay and offers **CONTINUE** on the next boot. Original files remain unchanged.

## Status and limitations

Both supported profiles reach first-person gameplay. Observed checks include startup, introduction, menus, keyboard input, persistent saves, QCP/QCELP sound decoding and optional MIDI rendering. Full campaign completion is unverified. **The Nokia collection only has its first two campaign packs.**

Earlier VX8350 measurements recorded roughly 19 gameplay ticks/s (about 38 fps); the emulated CPU performs the game's software rendering. Those are historical measurements, not a performance guarantee. Additional handset builds are inventoried but not qualified; LG VX10000 also needs EGL/GLES support. Some BREW classes remain unimplemented or refused.

See [coverage and selection rationale](docs/builds.md), [observed runtime results](docs/advanced-usage.md#status), and [research notes](docs/research.md).

## Development

| Command | Purpose |
| --- | --- |
| `make` | List available targets |
| `make venv` | Create the virtual environment and install dependencies |
| `make launcher` | Open the launcher |
| `make play` / `make landscape` | Launch portrait / landscape gameplay |
| `make test` | Run tests without game data |
| `make smoke` / `make mouse` | Check window/input / mouse behavior with game files |
| `make input` | Exercise real gameplay input with isolated saves |
| `make shot SECS=30` | Capture a frame after 30 seconds |
| `make clean` | Remove development caches |

Override paths with `make play GAME_DIR="/path/to/build" DATA="/path/to/data"` or `BIO3DPC_GAME_DIR` and `BIO3DPC_DATA_DIR`. Standalone tools use `.venv/bin/python tools/play.py --mod "/path/to/bio3d.mod" --data-dir "/path/to/data"`.

Before publishing, stage intended files and run `python3 tools/check_publish.py`; `python3 tools/check_publish.py --media` validates README media. CI runs the audit and tests without original files. See [publishing notes](docs/publishing.md), [detailed tools and source layout](docs/advanced-usage.md), and [reverse-engineering workflow](docs/rea-workflow.md).

## License and credits

BioShock and BioShock 3D belong to their respective rights holders. This unofficial preservation project has no affiliation or endorsement. Original modules, data, SDK headers, extracted sounds and fonts are not distributed here. Unicorn, Pillow and other dependencies retain their licenses.

README gameplay media contains rendered captures. Banner and launcher artwork are generated; see [banner provenance](docs/banner-art.md) and [launcher provenance](docs/launcher-art.md).
