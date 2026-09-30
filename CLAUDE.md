# Durable Project Rules

## Scope
- Offline use of the user's own copies only. Never add ownership-check bypasses, online-play enablement, executable patching, or redistribution support.
- Target one console only. Keep existing `title_id` handling unchanged; record the console's exact firmware, region, and title ID once in `docs/TEST-MATRIX.md` when supplied.
- Preserve PC Workshop/source files by default. No automatic cache deletion.
- FFPorter remains pinned to upstream v1.50 and changes remain an ordered patch series under `patches/`.
- `BO3-Customs.sprx` is a prebuilt upstream binary; do not rebuild or modify it without explicit approval.

## Safety
- Console writes are limited to `/data/BO3-Customs` plus the documented GoldHEN `config.ini` and `plugins.ini`, with backups before GoldHEN changes.
- Upload maps through a temporary filename and rename only after completion and verification.
- Never delete or rebuild the workdir, downloaded maps, zone index cache, or pulled PS4 zones automatically.
- Never commit game files, converted maps, Workshop content, SDK files, credentials, klog dumps, or screenshots with personal data.

## Working Rules
- Windows + PowerShell is the supported host environment.
- Python 3.10+ remains the orchestration/CLI language; C#/.NET remains the FFPorter language.
- Do not introduce another language/toolchain without measured benefit, a clean interface, CI, and documentation in `docs/ARCHITECTURE.md`.
- Keep existing CLI behavior backward compatible; additions are explicit.
- Use small, focused commits. Never force-push or rewrite history.
- Every compatibility result must be labeled with evidence: hardware, unit test, static analysis, or unverified.
- Do not mark work complete without implementation, available automated tests, hardware test where possible, documentation, and matrix evidence.
- Put extra ideas in `docs/IDEAS.md`, not production code.

## Hardware Protocol
- FTP is normally on 2121; klog is normally on TCP 3232 with GoldHEN TTYRedirect. Verify instead of assuming.
- T0: appears in CUSTOM. T1: loads to spawn with clean klog. T2: five or more rounds with core gameplay. T3: round 15+ or 30-minute soak. T4: UI/sound fidelity evidence.
- Start klog capture before launching BO3. Keep raw evidence outside Git.
- Back up before first console change and before risky changes. Provide rollback instructions for every console-changing operation.

## Required Commands / Docs
See `docs/PROGRESS.md` for the current resume point.
See `docs/REQUIREMENTS.md` for setup inventory.
See `docs/ARCHITECTURE.md` for the real pipeline and inferred behavior.
See `docs/PORTABILITY.md` for asset/blocker inventory.
See `docs/TEST-MATRIX.md` for hardware evidence.
See `docs/ANALYZER-CALIBRATION.md` for prediction-vs-outcome tuning.
See `docs/MODS-RESEARCH.md` for future-mod support.
See `docs/DECISIONS.md` for decisions and rejected alternatives.

## Session Rule
At the start of a session, read this file and `docs/PROGRESS.md`, then continue from the documented Next section. Update `docs/PROGRESS.md` after every meaningful step and before long jobs.
