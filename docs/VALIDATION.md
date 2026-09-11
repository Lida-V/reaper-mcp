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
