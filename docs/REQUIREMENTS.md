# Requirements

## Needs From User
- Up to 15 Workshop IDs covering: small, large, popular, known crashers, white-box HUD, custom perks, custom weapons, custom sounds/music, and one known-good map.
- `config.json` and the actual PC BO3 install path/SteamCMD path (paths only; never credentials).
- Upstream v1.50 `Console.zip` in `upstream\\`.
- Any local non-Workshop map samples and Mod Tools output used for `port-local` testing.
- Custom sound samples for audio tests.
- The exact map IDs already observed with white boxes, custom perks, custom weapons, or broken sound.
- One hardware baseline run with the console firmware, region, title ID, and tool versions recorded.

## Host software
| Dependency | Required version | Why | Doctor check |
|---|---|---|---|
| Python | 3.10+ | CLI/orchestration | planned |
| Git | current supported release | fetch/pinned upstream source | planned |
| .NET SDK | 10.x | FFPorter build | planned |
| SteamCMD | current installed build | Workshop download | planned |
| Optional psslc | user-local Sony SDK tool | missing PS4 technique sets | planned only when configured |

No additional runtime dependency is accepted until it has a measured benefit and a documented install/test path.

## Console
- GoldHEN 2.3+; verify exact installed version.
- BO3 v1.33; verify on the target console.
- BO3-Customs runtime pack in `/data/BO3-Customs`.
- GoldHEN plugins enabled and plugin files backed up before changes.
- TTYRedirect enabled for klog capture.
- FTP reachability and actual port verified.
- Free space on `/data` measured before uploads.

## PC capacity/network
- 16 GB RAM minimum; measure peak working set on large maps.
- About 40 GB workdir baseline plus retained cache growth.
- About 120 GB BO3 install, plus room for retained Workshop cache.
- Same LAN path to PS4; VPN off for the test path.
- Firewall permits FTP/klog traffic where needed.
