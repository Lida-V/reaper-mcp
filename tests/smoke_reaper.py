"""Opt-in integration test. Writes only generated test audio/project in output/."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]


def main():
    server = subprocess.Popen([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/reaper_mcp.py')],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding='utf-8')
    serial = 0

    def rpc(method, params):
        nonlocal serial
        serial += 1
        server.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': serial, 'method': method, 'params': params}) + '\n')
        server.stdin.flush()
        response = json.loads(server.stdout.readline())
        assert response.get('id') == serial and 'error' not in response, response
        return response['result']

    def tool(name, **arguments):
        result = rpc('tools/call', {'name': name, 'arguments': arguments})
        assert not result.get('isError'), result
        return json.loads(result['content'][0]['text'])

    output = ROOT / 'output' / ('smoke-' + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    # Lua long-bracket quoting does not interpret Windows paths as escapes.
    def lua_string(value):
        delimiter = '='
        value = str(value).replace('\\', '/')
        while ']' + delimiter + ']' in value:
            delimiter += '='
        return '[' + delimiter + '[' + value + ']' + delimiter + ']'

    try:
        rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                           'clientInfo': {'name': 'reaper-mcp-smoke', 'version': '1'}})
        boot_started = time.monotonic()
        tool('reaper_start')
        boot_seconds = time.monotonic() - boot_started
        wrong = rpc('tools/call', {'name': 'reaper_run_lua', 'arguments': {
            'code': 'return true', 'expected_project': '__mismatch__.rpp'}})
        assert wrong['isError'], 'Project guard should reject mismatched project'
        project = output / 'smoke.rpp'
        code = '''
assert(reaper.CountTracks(0)==0 and reaper.IsProjectDirty(0)==0,'Smoke test needs an empty clean project')
reaper.InsertTrackAtIndex(0,true)
local track=reaper.GetTrack(0,0)
reaper.GetSetMediaTrackInfo_String(track,'P_NAME','MCP test synth',true)
local fx=reaper.TrackFX_AddByName(track,'ReaSynth (Cockos)',false,-1)
assert(fx>=0,'ReaSynth did not load')
local count=reaper.TrackFX_GetNumParams(track,fx)
assert(count>0,'No FX parameters')
assert(reaper.TrackFX_SetParamNormalized(track,fx,0,0.25),'FX parameter write failed')
assert(math.abs(reaper.TrackFX_GetParamNormalized(track,fx,0)-0.25)<0.01,'FX parameter readback failed')
local item=reaper.CreateNewMIDIItemInProj(track,0,2,false)
local take=reaper.GetActiveTake(item)
local start=reaper.MIDI_GetPPQPosFromProjTime(take,0.1)
local finish=reaper.MIDI_GetPPQPosFromProjTime(take,1.7)
reaper.MIDI_InsertNote(take,false,false,start,finish,0,60,90,false)
reaper.MIDI_Sort(take)
reaper.SetMediaTrackInfo_Value(track,'D_VOL',0.25)
reaper.GetSetProjectInfo(0,'RENDER_SETTINGS',0,true)
reaper.GetSetProjectInfo(0,'RENDER_BOUNDSFLAG',0,true)
reaper.GetSetProjectInfo(0,'RENDER_STARTPOS',0,true)
reaper.GetSetProjectInfo(0,'RENDER_ENDPOS',2,true)
reaper.GetSetProjectInfo(0,'RENDER_SRATE',48000,true)
reaper.GetSetProjectInfo(0,'RENDER_CHANNELS',2,true)
reaper.GetSetProjectInfo(0,'RENDER_TAILFLAG',0,true)
reaper.GetSetProjectInfo_String(0,'RENDER_FORMAT','ZXZhdxgAAA==',true)
reaper.GetSetProjectInfo_String(0,'RENDER_FILE',OUTPUT,true)
reaper.GetSetProjectInfo_String(0,'RENDER_PATTERN','smoke',true)
reaper.UpdateArrange()
reaper.Main_SaveProjectEx(0,PROJECT,8)
local rendered=REAPER_SUPPORTER_RENDER()
reaper.Main_SaveProjectEx(0,PROJECT,8)
return {fx_parameters=count,notes=1,version=reaper.GetAppVersion(),rendered=rendered}
'''.replace('OUTPUT', lua_string(output)).replace('PROJECT', lua_string(project))
        result = tool('reaper_run_lua', code=code, timeout_seconds=120, expected_project='')
        assert project.is_file(), 'Project was not saved'
        assert result['project'].replace('\\', '/').lower() == str(project).replace('\\', '/').lower(), result['project']
        # An existing target must be an immediate error, never an overwrite dialog that blocks the worker.
        blocked_at = time.monotonic()
        exists = rpc('tools/call', {'name': 'reaper_run_lua', 'arguments': {
            'code': 'return REAPER_SUPPORTER_RENDER()', 'timeout_seconds': 60, 'expected_project': str(project)}})
        assert exists['isError'] and 'Render target exists' in exists['content'][0]['text'], exists
        assert time.monotonic() - blocked_at < 10
        tool('reaper_run_lua', code='return REAPER_SUPPORTER_RENDER({overwrite=true})', timeout_seconds=120,
             expected_project=str(project))
        # A job longer than the caller waits for: busy (not stopped), no duplicate start, result fetched later.
        job = tool('reaper_submit_lua', expected_project=str(project),
                   code='local t=reaper.time_precise(); while reaper.time_precise()-t<8 do end; return "slept"')['job_id']
        time.sleep(6.5)
        busy = tool('reaper_status')
        assert busy['state'] == 'busy' and not busy['responsive'] and busy['job_id'] == job, busy
        second = rpc('tools/call', {'name': 'reaper_start', 'arguments': {}})
        assert second['isError'] and 'No second instance' in second['content'][0]['text'], second
        finished = tool('reaper_job_result', job_id=job, wait_seconds=30)
        assert finished['state'] == 'done' and finished['result'] == 'slept', finished
        with wave.open(str(output / 'smoke.wav'), 'rb') as audio:
            channels, width, rate, frames = audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()
            samples = audio.readframes(frames)
        peak = max(abs(int.from_bytes(samples[i:i+width], 'little', signed=True))
                   for i in range(0, len(samples), width)) / (2 ** (width * 8 - 1))
        assert channels == 2 and rate == 48000 and width == 3, (channels, rate, width)
        assert 1.9 <= frames / rate <= 2.1 and 0.001 < peak < 0.99, (frames / rate, peak)
        tool('reaper_run_lua', code='return {tracks=reaper.CountTracks(0)}', expected_project=str(project))
        dirty = tool('reaper_run_lua', expected_project=str(project), code="reaper.GetSetMediaTrackInfo_String(reaper.GetTrack(0,0),'P_NAME','MCP changed',true); reaper.MarkProjectDirty(0); return true")
        refused = rpc('tools/call', {'name': 'reaper_stop', 'arguments': {}})
        assert refused['isError'], 'Unsaved project should prevent shutdown'
        tool('reaper_run_lua', expected_project=str(project), code='reaper.Main_SaveProject(0,false); return true')
        stopped = tool('reaper_stop')
        assert stopped['exited'], 'REAPER process did not exit'
        final_status = tool('reaper_status')
        assert final_status['state'] == 'not_started', final_status
        report = {'passed': True, 'reaper': result['result']['version'], 'channels': channels,
                  'startup_seconds': round(boot_seconds, 3),
                  'sample_rate': rate, 'bit_depth': width * 8, 'duration_seconds': frames / rate,
                  'peak_linear': peak, 'project_guard': 'passed', 'dirty_stop_guard': 'passed',
                  'fx_parameter_readback': 'passed', 'mcp_transport': 'passed',
                  'existing_render_target_guard': 'passed', 'busy_state_and_job_result': 'passed',
                  'duplicate_start_guard': 'passed'}
        (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, indent=2))
    finally:
        # Never discard or kill a failed test's project. Inspect the dedicated worker if it remains open.
        server.stdin.close()
        server.wait(timeout=10)
        stderr = server.stderr.read()
        if stderr:
            raise RuntimeError(stderr)


if __name__ == '__main__':
    main()
