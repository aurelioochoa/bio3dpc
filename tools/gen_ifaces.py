#!/usr/bin/env python3
"""Generate the BREW interface method tables used for vtable bookkeeping.

Parses the QUALCOMM BREW SDK headers and emits ``bio3dpc/ifaces_gen.py``: an
ordered mapping from interface name to method names.  The order is the binary
vtable layout, so a method's index in the tuple is the vtable slot index the
guest code will call.

Two header styles exist in the BREW 3.1.5 SDK:

1. ``#define INHERIT_<Iface>(iname) \\`` macro blocks (sdk/inc/AEEShell.h, ...).
2. Old-style ``QINTERFACE(<Iface>) { DECLARE_IBASE(...) ... };`` blocks
   (sdk/inc/AEEGraphics.h, ...).

Only names and order are extracted; no SDK source text is copied.

Usage:
    tools/gen_ifaces.py --sdk "/path/to/BREW 3.1.5 SP02" [--out bio3dpc/ifaces_gen.py]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

INHERIT_RE = re.compile(r"^#define\s+INHERIT_([A-Za-z0-9_]+)\s*\(\s*iname\s*\)\s*\\?\s*$")
QIFACE_RE = re.compile(r"^QINTERFACE\s*\(\s*([A-Za-z0-9_]+)\s*\)\s*$")
METHOD_RE = re.compile(r"\(\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")
INHERIT_USE_RE = re.compile(r"^(INHERIT_[A-Za-z0-9_]+|DECLARE_IBASE)\s*\(")

# Marker for an interface included inside another one; resolved in pass 2.
INHERIT_MARK = "\0INHERIT:"

RAW: dict[str, list[str]] = {}


def scan_file(path: Path) -> None:
    """Collect INHERIT_ macro bodies and QINTERFACE bodies from one header."""
    inherit_name: str | None = None
    inherit_body: list[str] = []
    qiface: str | None = None
    qiface_body: list[str] = []

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.rstrip()

        # Style 1: INHERIT_<Iface> macro, body lines end with backslash.
        if inherit_name is not None:
            inherit_body.append(line)
            if not line.endswith("\\"):
                RAW.setdefault(inherit_name, body_methods(inherit_body))
                inherit_name = None
                inherit_body = []
            continue

        # Style 2: QINTERFACE(<Iface>) { ... }; block.
        if qiface is not None:
            stripped = line.strip()
            if stripped.startswith("};") or stripped == "}":
                RAW.setdefault(qiface, body_methods(qiface_body))
                qiface = None
                qiface_body = []
            else:
                qiface_body.append(line)
            continue

        m = INHERIT_RE.match(line)
        if m:
            inherit_name = m.group(1)
            inherit_body = []
            continue
        m = QIFACE_RE.match(line)
        if m:
            qiface = m.group(1)
            qiface_body = []
    # Unterminated blocks: record what we have so tooling fails loudly, not silently.
    if inherit_name is not None:
        RAW.setdefault(inherit_name, body_methods(inherit_body))
    if qiface is not None:
        RAW.setdefault(qiface, body_methods(qiface_body))


def body_methods(body: list[str]) -> list[str]:
    methods: list[str] = []
    for line in body:
        line = line.rstrip("\\").strip()
        if not line or line.startswith("//"):
            continue
        use = INHERIT_USE_RE.match(line)
        if use:
            macro = use.group(1)
            base = "IBase" if macro == "DECLARE_IBASE" else macro[len("INHERIT_"):]
            methods.append(INHERIT_MARK + base)
            continue
        # Only the member declarator counts: ``ret (*Name)(params)``.  A
        # parameter may itself be a function pointer (``void (*pfn)(void*)``),
        # so take just the first ``(*name)`` of each ``;``-separated segment.
        for segment in line.split(";"):
            match = METHOD_RE.search(segment)
            if match:
                methods.append(match.group(1))
    return methods


SEED: dict[str, list[str]] = {"IBase": ["AddRef", "Release"]}


def resolve() -> dict[str, tuple[str, ...]]:
    resolved: dict[str, tuple[str, ...]] = {}

    def resolve_one(name: str, stack: tuple[str, ...]) -> list[str]:
        if name in SEED:
            return list(SEED[name])
        if name in resolved:
            return list(resolved[name])
        if name in stack:
            raise RuntimeError(f"cyclic inheritance: {' -> '.join(stack + (name,))}")
        out: list[str] = []
        for item in RAW.get(name, []):
            if item.startswith(INHERIT_MARK):
                out.extend(resolve_one(item[len(INHERIT_MARK):], stack + (name,)))
            else:
                out.append(item)
        resolved[name] = tuple(out)
        return out

    for name in list(RAW):
        resolve_one(name, ())
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path,
                        help="BREW SDK root, e.g. '/.../BREW 3.1.5 SP02'")
    parser.add_argument("--out", type=Path, default=Path("bio3dpc/ifaces_gen.py"))
    args = parser.parse_args()

    # sdk/inc holds the legacy headers (AEEShell.h, AEEDisp.h, ...); the
    # top-level inc/ holds the per-interface headers (AEEIDisplay.h, ...).
    # sdk/inc is scanned first: its IShell/IModule definitions were verified
    # against the binary ABI, and first definition wins.
    inc_dirs = [args.sdk / "sdk" / "inc", args.sdk / "inc"]
    if not inc_dirs[0].is_dir():
        print(f"not a BREW SDK root (missing sdk/inc): {args.sdk}", file=sys.stderr)
        return 2

    for inc in inc_dirs:
        if inc.is_dir():
            for header in sorted(inc.glob("*.h")):
                scan_file(header)
    table = resolve()
    if len(table.get("IShell", ())) != 52:
        print("IShell table mismatch; expected the verified 52-slot layout, "
              f"got {len(table.get('IShell', ()))}", file=sys.stderr)
        return 2

    lines = [
        '"""Generated by tools/gen_ifaces.py - do not edit by hand.',
        "",
        "Method names per BREW interface in vtable order.  Extracted from the",
        "BREW 3.1.5 SP02 SDK headers (sdk/inc + inc).  Index == vtable slot.",
        '"""',
        "",
        "# fmt: off",
        "IFACES: dict[str, tuple[str, ...]] = {",
    ]
    for name in sorted(table):
        methods = table[name]
        rendered = ", ".join(f'"{m}"' for m in methods)
        lines.append(f'    "{name}": ({rendered}),')
    lines.append("}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n")

    print(f"wrote {args.out}: {len(table)} interfaces")
    for probe in ("IBase", "IApplet", "IModule", "IShell", "IDisplay", "IBitmap",
                  "IGraphics", "IFileMgr", "IFile", "INetMgr", "ISocket",
                  "IMedia", "ISoundPlayer", "IHeap"):
        n = len(table.get(probe, ())) or None
        print(f"  {probe}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
