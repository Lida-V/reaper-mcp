# Validation

Release candidate: 0.1.0 / 2026-09-11

## Environment

- Windows, REAPER 7.79/x64, Python 3.13.
- Fresh dedicated configuration for the public export, reusing the existing FX index only.
- REAPER/ReaScript plus Python standard library. No Computer Use for synthesis, FX, MIDI, save, or render operations.
- Locally run tests; these are not claims of CI coverage or testing every client/OS/plugin.

## Automated tests

`python -m unittest discover -s tests -v`: 8 tests passed.

- Clean newline-delimited JSON-RPC stdio; no output for notifications.
- Initialize/version negotiation, tools/list, ping, malformed JSON and unknown method handling.
- Invalid Lua tool arguments rejected before calling REAPER.
- Execution failures returned as MCP tool errors.
- Existing local configuration preserved; only explicitly opted-in FX index caches copied.
- Synthetic test files confirm registration/license files are excluded from cache copying.
- A missing/empty FX index refuses launch before REAPER can perform a first-run system scan.
- Scan policy preserves other INI settings and non-UTF-8 Windows path bytes.

## Live MCP test

`python tests/smoke_reaper.py`: passed over the public export's stdio MCP server.

| Check | Observed result |
| --- | --- |
| Dedicated REAPER starts and reports API heartbeat | Passed |
| First startup with existing FX index | 12.544 seconds |
| Mismatched project path rejected | Passed |
| ReaSynth insertion | Passed |
| FX parameter write/readback | Passed |
| MIDI note inserted | Passed |
| RPP saved | Passed |
| WAV format | PCM, stereo, 48,000 Hz, 24-bit |
| WAV duration | 2.0 seconds |
| Linear sample peak | 0.088793; non-silent, no sample clipping |
| Unsaved edits prevent stop | Passed |
| Saved project and dedicated worker shutdown | Passed |

This is a transport/integration smoke test, not a perceptual mix/mastering quality test.
The synthesized WAV and RPP are generated locally under ignored `output/`; they are not shipped in this repository.

## Startup observation and limits

Starting a completely new profile without a prepared FX index triggered REAPER's initial system scan and could time out at 90 seconds.
The user observed and cancelled the VST scan. An empty search path, a placeholder path, and an empty index did not prevent REAPER from adding system VST3 paths during initialization.
The released setup therefore requires an existing index from an initialized REAPER resource folder and disables startup discovery (`vst_scan=1`). It refuses to start with a missing/empty index.
The final fresh-profile test passed in 18.19 seconds end to end, including the 12.544-second startup and audio rendering.
The three copied index files were byte-identical to the source after the test; no index rebuild was observed.
The process guard was also corrected to distinguish an exited Windows process whose handle remains available from an active process.

On startup failures, inspect the dedicated instance; do not repeatedly relaunch or force-kill an unrelated REAPER.
Use an index prepared for the same REAPER installation and plugins. New REAPER versions and changed plugin installations require separate verification.
Fresh REAPER installs, audio-device setup and plugin authorization can need manual attention.
No commercial plugin binaries, caches, licenses, original user profile, or production music are bundled.
macOS/Linux and exhaustive client/plugin compatibility have not been tested.

Configuration reference: [Ultraschall's REAPER configuration-variable documentation](https://mespotin.uber.space/Ultraschall/Reaper_Config_Variables.html#vst_scan). The live results above are local observations.

## 0.2.0 (2026-11-13): worker states, later results, render guard

Tested on Windows 11, REAPER 7.81 x64, Python 3.13.

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests` (no REAPER) | 13 passed |
| Job of 9 s polled at 7 s | `state=busy`, `responsive=false`, matching `job_id` |
| `start` and `run_lua` while busy | Refused; no second process |
| Result fetched after a 2 s timeout of a 4 s job | `job_result` returned it; nothing was resent |
| Six jobs submitted in a row | Ran in submission order |
| Wrong pinned project / `"*"` / `""` | Refused / passed / passed |
| Render to an existing target without `overwrite` | Error in 0.6 s, no dialog |
| Render with `overwrite=true` (action 42230) | File rewritten, 48 kHz 24-bit stereo, non-silent |
| Leftover `.request.lua` and `.running.lua` at start | Not executed; reported `cancelled` / `orphaned` |
| 4,608 finished queue files at start | Moved to `queue/done/`; scanned folder empty |
| Stop | Process exit confirmed before returning |

These live checks ran the same transport and bridge code in a dedicated profile that was already set up.
`tests/smoke_reaper.py` was extended with the render guard, the busy state and the later result, but its run in this checkout's own profile did not complete:
REAPER opened a dialog during startup and never loaded the bridge. `reaper_status` reported `stalled` and a second start was refused, as designed.
That profile had last been started two months earlier with an older REAPER version; the dialog was not identified. Run the first start of a profile with `--show-window`.

Limits found in this round:

- A dialog cannot be dismissed through this server. During a job it shows as `busy`, outside a job as `stalled`.
- With `vst_scan=1` and a prepared index, REAPER still added one newly installed plugin to the index at startup. Later starts left the index byte-identical. Startup therefore avoids a full scan, not every load of a plugin that is new since the index was prepared.
- The 600-second limits apply to a single wait. A longer render is waited for with repeated `reaper_job_result` calls; a render of several minutes was not part of this round.
