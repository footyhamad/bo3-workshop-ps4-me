# Architecture

## Verified static pipeline
1. `bo3ps4.py port` resolves a Workshop item through SteamCMD and locates its map fastfile.
2. FFPorter v1.50 receives the PC map, PC reference data, optional PS4 donor zones, and language selection.
3. FFPorter rebuilds PS4-layout assets and runs its converter/checking pipeline.
4. Python records converter output, copies map metadata/images, fills required language zones, and uploads to `/data/BO3-Customs/usermaps`.
5. Upstream `BO3-Customs.sprx` discovers maps when BO3 loads.

## Current verified implementation facts
- Python restricts normal FTP writes to `/data/BO3-Customs/` and separately permits the two documented GoldHEN plugin files.
- Upload already uses a `.part` temporary name followed by delete/rename; post-upload byte verification is still missing.
- `pull-zones` reads from the mounted BO3 zone path and copies `.ff`/ `.fd` files without writing to the console.
- FFPorter receives PS4 donor zones through `--donors` when the local PS4-zone cache exists.
- Existing patch code carries donor shader dependencies and supports nearest feature-dropping technique-set substitution when no PS4 shader compiler is available.
- Existing converter output includes `FFPORTER_FIDELITY` JSON events; Python currently reduces these to stage-change messages instead of exposing a stable machine-readable stream.

## Failure surfaces to instrument
- SteamCMD authentication/timeouts and cache validity.
- PS4 zone pull interruption/corruption.
- Shader/compiler availability and unsafe donor substitutions.
- Missing assets and unsupported GSC/CSC functions/opcodes.
- Texture/image conversion and UI-specific technique sets.
- Sound decoding/encoding, loop metadata and bank/alias mapping.
- Upload interruption, disk-full, or incomplete rename.
- Runtime crashes and klog evidence.

## Inference markers
Any behavior not confirmed by source or hardware must be labeled **inferred** in the phase documents and never presented as hardware-proven.
