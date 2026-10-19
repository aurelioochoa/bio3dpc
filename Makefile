# bio3dpc - development entry points.  Run `make` for the list.
#
# Point the tools at another build without editing anything:
#
#   make play GAME_DIR="/path/to/<build>" DATA="/path/to/data/vx8350"
#
# Variables that make the targets useful for experiments:
#   SECS     seconds to run before capturing / between series shots  (12)
#   AT       seconds to run before the first scripted key            (8)
#   KEYS     comma-separated keys for `keys`  (SELECT,WAIT,SOFT2,…)
#   SETTLE   seconds each key is held                                (4)
#   PLAN     keys for `auto`, one per screen settle  (SELECT,…)
#   BUDGET   seconds for `auto`                                      (240)
#   SCALE    window scale for `play`                                 (3)
#   OUT      where artifacts go  (/tmp/opencode/bio3dpc)

SHELL := /bin/bash

# Prefer the project virtualenv, fall back to the system interpreter.
PY ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

# Game build (module + the data set it loads its assets from).
GAME_DIR ?= $(shell $(PY) -c 'from bio3dpc.profiles import VX8350, discover_root; print(VX8350.mod_path(discover_root()).parent)')
DATA ?= $(shell $(PY) -c 'from bio3dpc.profiles import VX8350, discover_root; print(VX8350.data_path(discover_root()))')
MOD ?= $(GAME_DIR)/bio3d.mod

OUT ?= /tmp/opencode/bio3dpc
SECS ?= 12
AT ?= 8
KEYS ?= SELECT
SETTLE ?= 4
PLAN ?= SELECT,SELECT,SELECT,UP
BUDGET ?= 240
SCALE ?= 3

# The dev tools read these (tools/boot_check_paths.py).
export BIO3DPC_GAME_DIR := $(GAME_DIR)
export BIO3DPC_DATA_DIR := $(DATA)

.DEFAULT_GOAL := help
.PHONY: help venv test play launcher landscape install-desktop uninstall-desktop smoke mouse input shot keys auto series boot disasm \
        check-paths clean clean-artifacts

help:
	@echo "bio3dpc - development entry points"
	@echo
	@echo "  make venv        create .venv and install requirements.txt"
	@echo "  make test        run the test suite (no game data needed)"
	@echo "  make play        play the game in a window"
	@echo "  make launcher    open the animated Bathysphere launcher"
	@echo "  make landscape   play the Nokia 7705 landscape build"
	@echo "  make install-desktop  install a per-user applications-menu entry"
	@echo "  make uninstall-desktop  remove menu entry and command; preserve saves"
	@echo "  make smoke       headless check: window opens, frames draw, keys arrive"
	@echo "  make mouse       check the mouse: capture, look deltas, warps, buttons"
	@echo "  make input       real gameplay mouse/keyboard checks with isolated saves"
	@echo "  make shot        screenshot the device bitmap after SECS seconds"
	@echo "  make series      screenshot every SECS seconds"
	@echo "  make keys        scripted key session, one screenshot per step"
	@echo "  make auto        press keys whenever the screen settles"
	@echo "  make boot        report how far the build gets"
	@echo "  make disasm      disassemble OFFSET (hex) for LENGTH (hex) bytes"
	@echo "  make clean       remove caches"
	@echo "  make clean-artifacts  remove screenshots and logs from OUT"
	@echo
	@echo "  overrides: GAME_DIR=… DATA=… OUT=… SECS=… AT=… KEYS=… PLAN=… SCALE=…"

# --------------------------------------------------------------------- setup
venv:
	@test -x .venv/bin/python || { echo "creating .venv"; python3 -m venv .venv; }
	.venv/bin/python -m pip install -q -r requirements.txt
	@.venv/bin/python -c "import unicorn, capstone, pytest; \
	    print('ready:', '.venv/bin/python')"

test:
	$(PY) -m pytest -q

# ------------------------------------------------------------ run the game
check-paths:
	@test -f "$(MOD)" || { \
	    echo "bio3dpc: game module not found:"; echo "  $(MOD)"; \
	    echo "  bring your own BREW module and matching data; see README Installation"; \
	    echo "  open ./bio3dpc.sh and choose Installation, or set GAME_DIR and DATA"; \
	    exit 1; }
	@test -d "$(DATA)" || { \
	    echo "bio3dpc: data directory not found:"; echo "  $(DATA)"; \
	    echo "  set DATA=/path/to/data/vx8350 (see README Installation)"; \
	    exit 1; }

play: check-paths
	$(PY) tools/play.py --mod "$(MOD)" --data-dir "$(DATA)" --scale $(SCALE)

launcher:
	$(PY) -m bio3dpc

landscape:
	$(PY) tools/play.py --profile n7705 --scale $(SCALE)

install-desktop:
	$(PY) -m bio3dpc.desktop

uninstall-desktop:
	$(PY) -m bio3dpc.desktop --uninstall

smoke: check-paths
	$(PY) tools/frontend_check.py --warmup 4 --seconds 12 --scale 2

mouse: check-paths
	$(PY) tools/mouse_check.py --warmup 3 --scale 2

input: check-paths
	$(PY) tools/input_check.py

shot: check-paths
	@mkdir -p "$(OUT)"
	$(PY) tools/screenshot.py --seconds $(SECS) --out "$(OUT)/shot_$(SECS)s.png"
	@echo "wrote $(OUT)/shot_$(SECS)s.png"

series: check-paths
	@mkdir -p "$(OUT)"
	$(PY) tools/screen_series.py --seconds $(SECS) --step 5 --out "$(OUT)/series"

keys: check-paths
	@mkdir -p "$(OUT)"
	$(PY) tools/key_test.py --at $(AT) --settle $(SETTLE) --keys "$(KEYS)" \
	    --out "$(OUT)/keys"
	@echo "wrote $(OUT)/keys_*.png"

auto: check-paths
	@mkdir -p "$(OUT)"
	$(PY) tools/auto_play.py --plan "$(PLAN)" --budget $(BUDGET) \
	    --out "$(OUT)/auto"
	@echo "wrote $(OUT)/auto_*.png"

boot: check-paths
	$(PY) tools/boot_check.py --seconds $(SECS) --level info

# ----------------------------------------------------------------- tooling
OFFSET ?= 0x0
LENGTH ?= 0x100

disasm:
	$(PY) tools/disasm.py $(OFFSET) $(LENGTH) --mod "$(MOD)"

# ---------------------------------------------------------------- cleaning
clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
	@echo "caches removed"

clean-artifacts:
	@test -n "$(OUT)" || { echo "OUT is empty, refusing"; exit 1; }
	rm -rf "$(OUT)"
	@echo "removed $(OUT)"
