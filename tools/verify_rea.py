#!/usr/bin/env python3
"""Check retained REA evidence against original modules and Capstone."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bio3dpc.profiles import PROFILES, discover_root
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=Path('/tmp/bio3dpc-preservation/rea/evidence'))
    parser.add_argument('--library', type=Path, default=discover_root())
    args = parser.parse_args()
    for profile, directory, key_offset in zip(PROFILES, ('portrait', 'landscape'), (0x2c898, 0x2cccc)):
        source = profile.mod_path(args.library).read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        evidence = {}
        for name in ('load-image', 'key-query', 'camera', 'module-entry'):
            record = json.loads((args.evidence/directory/(name+'.json')).read_text())['structuredContent']
            assert record['subject']['digest']['sha256'] == digest == profile.sha256
            assert record['provider']['id'] == 'ghidra'
            assert record['evidence_id'].startswith('ev_')
            evidence[name] = record
        image = evidence['load-image']['normalized_result']
        assert image['status'] == 'verified' and image['file_bytes'] == len(source)
        assert image['base_address'] == '0x0' and image['architecture'] == 'arm'
        query = evidence['key-query']['normalized_result']
        assert int(query['procedure']['address'], 16) == key_offset
        decoded = list(Cs(CS_ARCH_ARM, CS_MODE_ARM).disasm(source[key_offset:key_offset+36], key_offset))
        addresses = [int(re.match(r'(0x[0-9a-f]+):', line)[1], 16) for line in query['assembly']]
        assert addresses == [instruction.address for instruction in decoded]
        assert len(decoded) == 9 and all(instruction.size == 4 for instruction in decoded)
        assert decoded[0].mnemonic == 'sub' and decoded[-1].mnemonic == 'bx'
        assert '0xe020' in query['pseudocode'].lower()
        assert '0x278' in evidence['camera']['normalized_result']['pseudocode']
        print(profile.id, 'verified mapping and 9 independently decoded key-query instructions')
    print('REA SOURCE EVIDENCE VERIFIED')


if __name__ == '__main__':
    main()
