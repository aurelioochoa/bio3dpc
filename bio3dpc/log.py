"""Run logging: human-readable console/file output plus a JSONL service trace.

The trace is the primary debugging artifact for this project: every guest call
into a host service is recorded with its raw arguments, so a crashed run can be
replayed from the trace without re-running the emulator.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_ANSI = {
    "debug": "\x1b[36m",   # cyan
    "info": "\x1b[32m",    # green
    "warn": "\x1b[33m",    # yellow
    "error": "\x1b[31m",   # red
    "dim": "\x1b[2m",
    "reset": "\x1b[0m",
}


class RunLog:
    def __init__(self, level: str | int = "info", path: str | Path | None = None,
                 trace_path: str | Path | None = None, stream=None):
        self.level = LEVELS[level] if isinstance(level, str) else int(level)
        self._stream = stream if stream is not None else sys.stderr
        self._file = None
        if path:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "a", encoding="utf-8")
        self._trace = None
        if trace_path:
            trace_path = Path(trace_path)
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            self._trace = open(trace_path, "w", encoding="utf-8")
        self._t0 = time.monotonic()
        self._seq = 0
        self._color = (os.environ.get("NO_COLOR") is None
                       and getattr(self._stream, "isatty", lambda: False)())

    def _paint(self, level: str, text: str) -> str:
        if not self._color or level == "dim":
            return text
        return f"{_ANSI[level]}{text}{_ANSI['reset']}"

    def log(self, level: str, message: str, **fields) -> None:
        if LEVELS[level] < self.level:
            return
        stamp = f"{time.monotonic() - self._t0:9.3f}s"
        extra = ""
        if fields:
            extra = " " + " ".join(f"{k}={v}" for k, v in fields.items())
        label = f"{level.upper():5s}"
        line = f"{stamp} {self._paint(level, label)} {message}{extra}"
        print(line, file=self._stream, flush=True)
        if self._file:
            # File copy without ANSI colour codes.
            plain = f"{stamp} {level.upper():5s} {message}{extra}"
            self._file.write(plain + "\n")
            self._file.flush()

    def debug(self, message: str, **fields) -> None:
        self.log("debug", message, **fields)

    def info(self, message: str, **fields) -> None:
        self.log("info", message, **fields)

    def warn(self, message: str, **fields) -> None:
        self.log("warn", message, **fields)

    def error(self, message: str, **fields) -> None:
        self.log("error", message, **fields)

    def trace(self, kind: str, name: str, args=None, ret=None, **extra) -> None:
        if not self._trace:
            return
        self._seq += 1
        record = {
            "seq": self._seq,
            "ms": round((time.monotonic() - self._t0) * 1000, 3),
            "kind": kind,
            "name": name,
        }
        if args is not None:
            record["args"] = list(args)
        if ret is not None:
            record["ret"] = ret
        record.update(extra)
        self._trace.write(json.dumps(record) + "\n")
        self._trace.flush()

    def close(self) -> None:
        for handle in (self._trace, self._file):
            if handle:
                handle.close()
        self._trace = None
        self._file = None
