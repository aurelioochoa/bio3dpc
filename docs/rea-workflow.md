# REA investigation

This work used the requested
[Reverse Engineer Anything skill](https://github.com/morluto/rea/blob/main/.agents/skills/reverse-engineer-anything/SKILL.md)
version 42, REA CLI **6.5.0**, Ghidra **12.1.4**, and Temurin JDK
**21.0.12.1+1**. REA is a development dependency only. Playing or launching
the game requires none of Node, Java, Ghidra, or REA.

The existing RE4 workflow's temporary toolchain had disappeared. The CLI was
reacquired through `npx -y rea-agents@latest`, and the required native engine
and JDK were extracted under `/tmp/bio3dpc-preservation/rea/toolchain/`.
No agent MCP registration or agent configuration was changed; a private
stdio session was used and closed after each binary investigation.

Downloaded archive SHA-256 identities were checked against the previously
documented RE4 baseline:

- Ghidra: `ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db`
- JDK: `ce79869e1307ed8ee1e2baa86a412b1eb5b75d10a01006d788a6f968bcfaee94`

## Mapping and reproduction

The BREW modules are headerless ARM application images for REA's purposes.
The supported raw-binary lane was selected explicitly with architecture
`arm` and file offset zero mapped to `0x0`. REA's independent load-image
verification reports the entire original file mapping and both original and
snapshot digests as verified. No ELF wrapper or modified game image was used.
Runtime addresses equal `ArmMachine.load_base + file offset`; static addresses
below are file offsets. The real module cold entry relocates itself at runtime.

With an existing toolchain, a focused query can be reproduced as:

```sh
GHIDRA_INSTALL_DIR=/external/ghidra_12.1.4_PUBLIC \
JAVA_HOME=/external/jdk-21.0.12.1+1 \
rea function /path/to/bio3d.mod 0x2cccc \
  --target-format raw-binary --architecture arm --entry-point 0x0 \
  --provider ghidra --json > /external/nokia-key-query.json
```

The investigation used seed files naming the cold entry, module entry at
`0x200`, key-query candidate and camera-input candidate. Candidate names and
addresses were caller assertions from Capstone matching, not independent
Ghidra discoveries. Ghidra then supplied actual function bodies, instructions,
decompilation and mapping evidence. Capstone 5.0.6 independently decoded the
36-byte key-query functions from the original module bytes.

## Findings and evidence

Complete evidence is retained outside the checkout under
`/tmp/bio3dpc-preservation/rea/evidence/{portrait,landscape}/`. Each directory
contains open, load-image, key-query, camera, module-entry and close records.
Private tooling and seeds are in the parent directory.

| Record | Evidence ID |
| --- | --- |
| Portrait load image | `ev_6abac14ec88d82485ebcfad1f777fb7b545e2eff06600efad37b29449a57eff2` |
| Portrait key query | `ev_4edcad68b3730af4c42fc0b21c52d4c052845f0b30136028d9b8f60767574388` |
| Portrait camera | `ev_6393a103c36d40c31424e060c580d88ec0d442f6e2397f28ed3ee8906dec087e` |
| Landscape load image | `ev_e293dd77db91b8cc0dd2c651ea393d1de541a72c20ab2a1e3fca9dfd70d61c03` |
| Landscape key query | `ev_1da53c942e7199938216cec3fb77444d90bdcdc1bf7cfb4f74606556cf468e56` |
| Landscape camera | `ev_237f70391979f4716b8ad04a525a81cc74c665f043d5bf63e68c2e78a992fd0c` |

Both key-query functions normalize the phone key by subtracting `0xE020`,
truncate the result to 16 bits, and read a four-byte state at object offset
`4 + index * 4` when the index is in range. The upper bounds differ: 99
entries for VX8350 and 79 for Nokia. These are observations from decompilation
and independent instruction decoding; they do not establish live input timing.

Camera decompilation shows both builds using the player heading at `+0x278`
and camera pointer at `+0x2B8`. The engine input-object offset is `+0x1C8`
for VX8350 and `+0xB4` for Nokia, corroborating the 0x114 context-layout shift.
Native numeric look keys and the fixed `0xA0000` heading step are shared.

## Limits

Ghidra uses generic `ARM:LE:32:v7` for this raw lane. That analysis convention
does not identify the original compiler, exact phone CPU, or original source
types. Signatures and variable names are recovered with low confidence.
Indirect call targets remain unresolved and camera call-graph coverage is
partial. Static analysis does not establish gameplay speed, input delivery,
later-level compatibility or asset completeness. Those require separate live
checks. The external evidence is temporary local analysis, not a redistributable
game asset or an enduring checked-in Ghidra project.
