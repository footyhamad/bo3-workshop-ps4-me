# Portability Inventory

Status values: **fixed**, **partial**, **blocked**, **unverified**.

| Area | Current evidence | Status | Next evidence |
|---|---|---|---|
| Models/materials | FFPorter handles standard T7 asset conversion; donor technique sets are used for missing shaders. | partial | corpus + hardware |
| Images/textures | FFPorter has a dedicated image converter and XPak format handling. White-box root causes are not yet isolated here. | unverified | T4 UI audit |
| Technique sets/shaders | Exact donor and nearest feature-dropping substitution exist in current patches; Sony compiler path is optional upstream capability. | partial | A/B hardware test |
| GSC | Existing data fixes cover WaitTill and clearallcharactertables. Broader PC-only coverage is not yet enumerated. | partial | analyzer + hardware |
| CSC/UI scripts | Upstream includes UI/LUI compatibility shims; project does not yet inventory unsupported differences. | unverified | source audit + hardware |
| String/localization | Language-zone duplication exists for console languages. | partial | corpus hardware |
| Sounds/music/voice | FFPorter v1.50 converts supported FLAC/MP3 through its sound path; custom-sound failures are not yet exhaustively classified. | partial | ground-truth audio tests |
| HUD/fonts/LUI | White boxes reported by user/mission; no reproduced evidence in this run. | unverified | T4 reproduction |
| DLC/Zombies Chronicles assets | Existing compatibility notes cite absent DLC assets as a crash/conversion cause. | partial | controlled missing-asset logs |
| Custom perks/weapons | No project-level compatibility audit yet. | unverified | content analyzer + T2 |
| Local non-Workshop maps | No local-source abstraction/CLI yet. | blocked | Phase 3 |
