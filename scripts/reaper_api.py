"""Local REAPER API transport. Python stdlib only; no UI automation."""
from pathlib import Path
import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import msvcrt

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config/local.json'
RUNTIME = ROOT / 'runtime'
QUEUE = RUNTIME / 'queue'
RESOURCE = RUNTIME / 'reaper-resource'

def config():
    if not CONFIG.exists():
        raise RuntimeError('Run python scripts/configure.py --reaper PATH --plugin-cache-source RESOURCE before starting REAPER.')
    return json.loads(CONFIG.read_text(encoding='utf-8-sig'))

def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

@contextlib.contextmanager
def lock():
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME/'transport.lock').open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise RuntimeError('Another REAPER supporter request is active; wait for its result.') from e
        try: yield
        finally:
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

def status():
    path=RUNTIME/'bridge-status.json'
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        data['heartbeat_age_seconds']=time.time()-data['updated']
        data['responsive']=0 <= data['heartbeat_age_seconds'] < 5
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {'responsive':False,'state':'not_started'}

def initialize_resource():
    cfg=config()
    RESOURCE.mkdir(parents=True,exist_ok=True); QUEUE.mkdir(parents=True,exist_ok=True)
    ini=RESOURCE/'reaper.ini'
    if not ini.exists():
        # Use a separate resource configuration, without the streaming ASIO settings.
        ini.write_text('[reaper]\nlastproject=\nprojecttabs=0\nloadlastproj=0\n'
                       'audiomode=0\nwaveout_driver_in=-1\nwaveout_driver_out=-1\n'
                       'vst_scan=1\n'
                       'renderclosewhendone=4\nwnd_state=0\n',encoding='utf-8')
    # Registration/license identity must never be copied into the supporter.
    cache = RESOURCE / 'reaper-vstplugins64.ini'
    source_path = cfg['target'].get('pluginCacheSource')
    cache_ready = cache.exists() and any(b'=' in line for line in cache.read_bytes().splitlines())
    if source_path and not cache_ready:
        source = Path(source_path)
        for name in ('reaper-vstplugins64.ini','reaper-vstshells64.ini','reaper-vstbridge64.ini'):
            src=source/name
            if src.is_file(): shutil.copy2(src,RESOURCE/name)
    if not cache.exists():
        cache.write_text('[vstcache]\n', encoding='utf-8')
    # Disable startup discovery; do not silently rebuild a missing plugin index.
    # Existing index entries may still be loaded explicitly by ReaScript.
    set_reaper_ini_options(ini, {'vst_scan': '1'})
    return ini

def require_prepared_plugin_cache():
    cache = RESOURCE / 'reaper-vstplugins64.ini'
    if not cache.exists() or not any(b'=' in line and not line.startswith(b'[')
                                     for line in cache.read_bytes().splitlines()):
        raise RuntimeError('A prepared FX index is required to avoid first-run system scans. '
                           'Set target.pluginCacheSource in config/local.json to an initialized '
                           'REAPER resource directory, then retry. No REAPER process was launched.')

def set_reaper_ini_options(path, options):
    # REAPER may persist non-UTF-8 paths on Windows. Preserve all unrelated bytes.
    text = path.read_bytes()
    section = re.search(rb'(?ims)^\[reaper\]\r?\n(.*?)(?=^\[|\Z)', text)
    if not section:
        raise RuntimeError('Dedicated REAPER configuration has no REAPER section')
    body = section.group(1)
    for key, value in options.items():
        key, value = key.encode('ascii'), value.encode('ascii')
        pattern = rb'(?im)^' + re.escape(key) + rb'=[^\r\n]*'
        if re.search(pattern, body):
            body = re.sub(pattern, lambda _: key + b'=' + value, body)
        else:
            body = body.rstrip(b'\r\n') + b'\n' + key + b'=' + value + b'\n'
    updated = text[:section.start(1)] + body + text[section.end(1):]
    if updated != text:
        path.write_bytes(updated)

def start(timeout=90, show_window=False):
    with lock():
        s=status()
        if s.get('responsive'): return s
        state=RUNTIME/'instance.json'
        if state.exists():
            # Do not launch a duplicate when an earlier worker is stalled or rendering.
            pid=json.loads(state.read_text())['pid']
            import ctypes
            kernel=ctypes.windll.kernel32
            kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
            kernel.OpenProcess.restype=ctypes.c_void_p
            kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if handle:
                exit_code=ctypes.c_uint32()
                try:
                    readable=kernel.GetExitCodeProcess(handle,ctypes.byref(exit_code))
                    active=not readable or exit_code.value == 259  # STILL_ACTIVE
                finally:
                    kernel.CloseHandle(handle)
                if active:
                    raise RuntimeError(f'Previous worker PID {pid} is still active; inspect it before restarting.')
        ini=initialize_resource()
        require_prepared_plugin_cache()
        # Requests left by a killed client must never execute on a later start.
        for pending in QUEUE.glob('*.request.lua'):
            pending.rename(pending.with_suffix('.cancelled'))
        (RUNTIME/'bridge-status.json').unlink(missing_ok=True)
        si=subprocess.STARTUPINFO(); si.dwFlags|=subprocess.STARTF_USESHOWWINDOW; si.wShowWindow=1 if show_window else 0
        args=[config()['target']['executable'],'-newinst','-noactivate','-nosplash','-cfgfile',str(ini),'-new',str(ROOT/'scripts/launch_bridge.lua')]
        p=subprocess.Popen(args,startupinfo=si,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        atomic_json(state,{'pid':p.pid,'created':time.time(),'args':args})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            s=status()
            if s.get('responsive'): return s
            if p.poll() is not None: raise RuntimeError(f'REAPER exited with code {p.returncode}')
            time.sleep(.25)
        raise TimeoutError(f'No API heartbeat after {timeout}s; worker PID {p.pid}. No retry was started.')

def run_lua(code,timeout=60,expected_project=None):
    with lock():
        if not status().get('responsive'): raise RuntimeError('Worker is not responsive; start or inspect it first.')
        job=uuid.uuid4().hex
        prefix=''
        if expected_project is not None:
            # Lua long literal delimiter selected to avoid source injection through a path.
            delim='='
            while ']'+delim+']' in expected_project: delim+='='
            lit='['+delim+'['+expected_project+']'+delim+']'
            prefix=f"local _,p=reaper.EnumProjects(-1,''); assert(p:gsub('\\\\','/'):lower()==({lit}):gsub('\\\\','/'):lower(),'Unexpected active project')\n"
        temp=QUEUE/f'{job}.tmp'
        temp.write_text(prefix+code,encoding='utf-8',newline='\n')
        os.replace(temp,QUEUE/f'{job}.request.lua')
        deadline=time.monotonic()+timeout
        path=QUEUE/f'{job}.result.json'
        while time.monotonic()<deadline:
            if path.exists():
                # Lua may truncate a UTF-8 source filename mid-character in a traceback.
                # Keep structured fields readable while replacing only malformed bytes.
                result=json.loads(path.read_text(encoding='utf-8',errors='replace'))
                result['job_id']=job
                if not result['ok']: raise RuntimeError(json.dumps(result,ensure_ascii=False))
                return result
            time.sleep(.1)
        pending=QUEUE/f'{job}.request.lua'
        try:
            pending.rename(QUEUE/f'{job}.cancelled.lua')
        except FileNotFoundError:
            pass
        else:
            raise TimeoutError(f'Job {job} was not picked up; cancelled pending request.')
        raise TimeoutError(f'Job {job} may still be running; inspect {path} before resubmitting.')

def list_fx(query=''):
    result=run_lua("local a={}; local i=0; while true do local ok,n,id=reaper.EnumInstalledFX(i); if not ok then break end; a[#a+1]={name=n,identifier=id}; i=i+1 end; return a")['result']
    if isinstance(result,dict): result=[]
    return [x for x in result if query.lower() in x['name'].lower()]

def stop():
    result=run_lua("local i=0; while true do local p=reaper.EnumProjects(i,''); if not p then break end; assert(reaper.IsProjectDirty(p)==0,'Save every project tab before stopping'); i=i+1 end; REAPER_SUPPORTER_STOP(); return {stopping=true}")
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and (RUNTIME/'bridge-status.json').exists(): time.sleep(.1)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['status','start','stop','list-fx','run-lua'])
    parser.add_argument('--file',type=Path); parser.add_argument('--query',default='')
    parser.add_argument('--timeout',type=int,default=60); parser.add_argument('--expected-project')
    parser.add_argument('--show-window',action='store_true',help='Show dedicated REAPER for initial setup/dialogs')
    args=parser.parse_args()
    if args.operation=='start': result=start(args.timeout,args.show_window)
    elif args.operation=='status': result=status()
    elif args.operation=='stop': result=stop()
    elif args.operation=='list-fx': result=list_fx(args.query)
    else:
        if not args.file: parser.error('--file is required')
        result=run_lua(args.file.read_text(encoding='utf-8-sig'),args.timeout,args.expected_project)
    print(json.dumps(result,ensure_ascii=False,indent=2))
