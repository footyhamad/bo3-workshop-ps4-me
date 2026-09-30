# Configuration

`config.example.json` contains the complete supported schema. Copy it to `config.json` and keep the real file local; `config.json` is intentionally not committed because it can contain local network and account information.

## Fields

- `ps4_ip`: PS4 FTP address. Required.
- `ftp_port`: FTP port, normally `2121`.
- `title_id`: PS4 title ID or `auto`.
- `workdir`: working directory for output, caches, converter state, backups and compatibility data. Empty defaults to `<repo>\workdir` for backward compatibility.
- `cache_dir`: retained Workshop source cache. Empty defaults to `<workdir>\cache\maps`.
- `keep_cache`: retain managed Workshop downloads; the pipeline never deletes them implicitly.
- `ps4_zones`: local copy of PS4 zones used as shader donors. Empty defaults to `<workdir>\ps4-zones`.
- `pc_game`: PC BO3 directory. Empty enables Steam library discovery.
- `steamcmd`: SteamCMD executable. Required.
- `steam_user`: SteamCMD account name, or omit it to use saved credentials.
- `ffport`: patched FFPorter executable. Empty defaults to `ffport\ffport.exe` in the repo.
- `psslc_path`: optional path to a locally installed `orbis-wave-psslc.exe`. The SDK is never bundled or downloaded.
- `upstream_release`: location of the upstream v1.50 runtime/source material. Empty defaults to `upstream` in the repo.
- `min_free_gb`: minimum free space required before conversion.
- `strict_mode`: strictness switch; `false` keeps compatibility-friendly defaults.
- `progress`: `auto`, `tty`, `plain` or `json`.
- `parallelism`: conversion concurrency setting; `1` is the safe default.

## Complete example

See `config.example.json` for the exact JSON template used by the current CLI.
