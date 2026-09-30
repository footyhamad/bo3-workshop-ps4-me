# Decisions

- **Use main on the user's fork.** Alternatives: feature branch or blocked state. Why: the user explicitly authorized direct main writes after the connector branch API returned 403.
- **Keep FFPorter pinned to v1.50.** Alternatives: move to newer upstream. Why: the mission fixes the patch-series baseline to v1.50; changing it would invalidate evidence.
- **Do not auto-delete Workshop content.** Alternatives: retain old cleanup behavior. Why: mission priority is data/cache safety and the user explicitly requires opt-in cleanup only.
- **Treat psslc as optional.** Alternatives: require SDK/compiler or invent a translator. Why: upstream already has an optional PS4 shader compiler path; the compiler must remain local and unbundled.
- **Do not modify/rebuild the SPRX.** Alternatives: fork its code and ship a new binary. Why: the mission fixes it as a prebuilt upstream binary pending explicit approval.
