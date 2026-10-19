"""Shared paths for the dev tools (game build + full data set).

The defaults discover the user's collection and select LG VX8350. Override
them with ``BIO3DPC_GAME_DIR`` / ``BIO3DPC_DATA_DIR`` (the Makefile does that
from ``GAME_DIR=`` / ``DATA=``) instead of editing this file.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bio3dpc.profiles import VX8350, discover_root

DEFAULT_GAME_DIR = str(VX8350.mod_path(discover_root()).parent)
DEFAULT_DATA_DIR = str(VX8350.data_path(discover_root()))

GAME_DIR = os.environ.get("BIO3DPC_GAME_DIR") or DEFAULT_GAME_DIR
DATA_DIR = os.environ.get("BIO3DPC_DATA_DIR") or DEFAULT_DATA_DIR
