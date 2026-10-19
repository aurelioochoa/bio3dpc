#!/usr/bin/env python3
"""Extract resources from a BioShock 3D .dat container (research format).

Record layout (verified earlier): 12-byte header [u32 0x00040001][u32 count]
[u32 prio], then records: 2-byte pad, [u32 payload][u32 name_len][name]
[u32 blob_offset][u32 index]; the last record omits index+separator.
Blob: [u16 0x0008][u32 size][u16 typecode] + payload (typecode 1=BMP, 4=PNG).
"""
from __future__ import annotations

import argparse
import os
import struct
import sys


def parse(path: str):
    data = open(path, "rb").read()
    magic, count, prio = struct.unpack_from("<3I", data, 0)
    print(f"{path}: magic=0x{magic:08x} count={count} prio={prio}")
    offset = 12
    records = []
    for index in range(count):
        pos = offset
        # 2-byte separator/pad before each record
        pad = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        payload, name_len = struct.unpack_from("<II", data, pos)
        pos += 8
        name = data[pos:pos + name_len].decode("latin-1")
        pos += name_len
        blob_off, rec_index = struct.unpack_from("<II", data, pos)
        pos += 8
        if index == count - 1:
            # last record omits index + separator: re-read conservatively
            blob_off = struct.unpack_from("<I", data, offset + 2 + 8 + name_len)[0]
            pos = offset + 2 + 8 + name_len + 4
        records.append({
            "name": name, "payload": payload, "blob_off": blob_off,
            "pad": pad, "index": rec_index, "record_at": offset,
        })
        offset = pos
    return data, records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dat")
    parser.add_argument("--out", default="/tmp/opencode/bio3dpc/resources")
    args = parser.parse_args()

    data, records = parse(args.dat)
    os.makedirs(args.out, exist_ok=True)
    kinds: dict[str, int] = {}
    for rec in records:
        off = rec["blob_off"]
        magic, size, typecode = struct.unpack_from("<HIH", data, off)
        payload = data[off + 8:off + 8 + size]
        ext = os.path.splitext(rec["name"])[1].lower().lstrip(".") or "bin"
        kinds[ext] = kinds.get(ext, 0) + 1
        safe = rec["name"].replace("/", "_")
        path = os.path.join(args.out, f"{rec['index']:04d}_{safe}")
        with open(path, "wb") as handle:
            handle.write(payload)
        if ext in ("png", "bmp"):
            print(f"  [{rec['index']:03d}] {rec['name']} blob@0x{off:x} "
                  f"magic=0x{magic:04x} tc={typecode} size={size} "
                  f"head={payload[:12].hex()}")
    print("kinds:", dict(sorted(kinds.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
