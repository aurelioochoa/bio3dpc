# Bathysphere launcher artwork and animation

The supplied reference is `design-concepts/01-bathysphere.png`. Its aged brass,
bakelite, oxidized green panel and underwater city define the implementation.
The user's requested subtitle is **BREW PC PORT**. The concept remains the
visual reference; `launcher-preview.png` and `launcher-narrow-preview.png`
show the actual implemented interface.

All visible control artwork was created with the built-in image generation
tool using the supplied concept as a reference. Final prompts are recorded in
`launcher-asset-prompts.md`. Masters are saved in `bio3dpc/ui_assets/`:

| Master | Use |
| --- | --- |
| `bathysphere-source.png` | Clean background plate, with no embedded controls |
| `bathysphere-logo-source.png` | Transparent BioShock 3D / BREW PC PORT lettering and skyline |
| `bathysphere-controls-source.png` | Brass play plaque, secondary button, edition frames, slider trough and thumb, switch housing and knob, amber selector |
| `bathysphere-icons-source.png` | Settings gear, saved-game folder, arrows and mouse symbol |
| `icon.png` | Existing porthole desktop icon |

`tools/build_launcher_assets.py` crops the inspected atlases into reusable
transparent sprites under `bio3dpc/ui_assets/controls/`; `manifest.json` records
the size and source of every sprite. It also crops the two generated edition
previews from the selected concept. Those images are illustrations, **not game
captures**. No original game assets are included in the launcher artwork.

Labels remain live text. Pointer hover, press, disabled state, selection and
keyboard focus are composed by `launcher_controls.py`, using the generated
textures. Button presses depress the image; a warm rim indicates hover and a
dashed outline indicates keyboard focus. Slider dragging clamps to scales
1–6; its arrows and keyboard controls step by one. The audio switch changes
the existing audio preference. All controls operate over the background plate.

Pillow resizes the artwork with antialiasing. Wide windows show the scene and
control panel side by side, preserving the title's proportions and covering
the scene viewport without stretching it. Below 820 pixels of content width,
the scene stacks above the controls. The panel fits the available height where
possible and scrolls in shorter windows; keyboard focus scrolls into view.
The minimum window size is 640×520. Resizing is debounced and uses the still
plate while a decoder for the new viewport starts.

`tools/build_launcher_video.py` creates a 1152×768, 60 fps, twelve-second H.264
loop. Bicubic water refraction moves by fractional pixels instead of jumping
between integer crop offsets. Drifting particles and small instrument-needle
movements follow periodic paths. Each water frame is sampled from the immutable
source plate with Pillow; FFmpeg composites the motion overlay and encodes it. The vector motion overlay is retained as
`bathysphere-motion.ass`, with `bathysphere-still.png` as the static fallback.
The video is silent and local; no generative video service is used.

Lamp flicker is computed separately at runtime. Four lamps receive independent,
unseeded schedules with irregular quiet intervals and short bursts of dimming
and recovery. Smooth interpolation and precomputed local alpha masks keep the
light changes soft and confined to the fixtures. Flicker never repeats with the
video and never changes the brightness of the text or controls. Disabling
animation stops both the video and flicker, and persists that preference.

FFmpeg decodes asynchronously; only the newest frame is retained. A monotonic
60 Hz presentation clock avoids accumulating timer drift. The UI thread never
waits on a video read. Decoder processes stop while a game runs, when animation
is disabled, and when the launcher closes. Without FFmpeg, the still artwork and
all controls remain usable.

```sh
.venv/bin/python tools/build_launcher_assets.py
.venv/bin/python tools/build_launcher_video.py
DISPLAY=:96 .venv/bin/python tools/launcher_check.py
```

The native check uses isolated preferences and saves, exercises pointer and
keyboard input, settings, save-folder dispatch, animation lifecycle, multiple
wide and narrow sizes, and captures the two screenshots. Measured results are
saved in `launcher-verification.json`. Automated tests also decode the entire
clip to check motion continuity and the loop seam, check flicker scheduling,
validate transparent assets and run the existing launcher/guest regressions.
