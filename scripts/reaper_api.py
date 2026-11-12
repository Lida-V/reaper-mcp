"""Local REAPER API transport. Python stdlib only; no UI automation."""
from pathlib import Path
import argparse
import contextlib
import ctypes
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
RETENTION_DAYS = 14            # finished job evidence older than this is removed at start()
JOB_ID = re.compile(r'[a-f0-9]{8,64}')

def done_dir():
    # Finished jobs live here; the bridge scans only QUEUE itself.
    return QUEUE / 'done'

def pin_file():
    return RUNTIME / 'pinned-project.json'

def config():
    if not CONFIG.exists():
        raise RuntimeError('Run python scripts/configure.py --reaper PATH --plugin-cache-source RESOURCE before starting REAPER.')
    return json.loads(CONFIG.read_text(encoding='utf-8-sig'))

def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def read_json(path, attempts=20, missing_attempts=1):
    """Read a JSON file the bridge may be replacing right now.
    A share violation or a half-seen replace is transient: retry here instead of in every caller.
    Returns None when the file is absent."""
    missing = 0; last = None
    for _ in range(attempts):
        try:
            # Lua may truncate a UTF-8 source filename mid-character in a traceback.
            # Keep structured fields readable while replacing only malformed bytes.
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
        except FileNotFoundError:
            missing += 1
            if missing >= missing_attempts: return None
        except (PermissionError, json.JSONDecodeError) as e:
            last = e
        time.sleep(.05)
    raise RuntimeError(f'Cannot read {path.name}: {last}')

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

def process_alive(pid):
    """True only while this PID is a running process with the configured REAPER executable name."""
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                                                 ctypes.POINTER(ctypes.c_uint32)]
    handle = kernel.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle: return False
    try:
        code = ctypes.c_uint32()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:
            return False                                   # exited; a handle elsewhere keeps the PID visible
        buf = ctypes.create_unicode_buffer(32768); size = ctypes.c_uint32(len(buf))
        if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            # The PID may have been reused by an unrelated program.
            try: expected = Path(config()['target']['executable']).name.lower()
            except (RuntimeError, KeyError): return True
            return Path(buf.value).name.lower() == expected
        return True
    finally:
        kernel.CloseHandle(handle)

def status():
    """state: idle (answers now) / busy (a job is executing; no heartbeat during it) /
    stalled (process exists, no heartbeat and no job: loading, a modal dialog, or hung) / not_started."""
    # The bridge removes then renames the heartbeat file: a short gap is not "stopped".
    data = read_json(RUNTIME/'bridge-status.json', missing_attempts=4) or {}
    inst = read_json(RUNTIME/'instance.json') or {}
    pid = inst.get('pid')
    alive = bool(pid) and process_alive(pid)
    responsive = False
    if data:
        data['heartbeat_age_seconds'] = time.time() - data['updated']
        responsive = 0 <= data['heartbeat_age_seconds'] < 5
    job = read_json(RUNTIME/'bridge-job.json') if (alive or responsive) else None
    if job:
        data['job_id'] = job.get('job')
        data['job_elapsed_seconds'] = time.time() - job.get('started', time.time())
    if job: state = 'busy'
    elif responsive: state = 'idle'
    elif alive: state = 'stalled'
    else: state = 'not_started'
    data.update(responsive=responsive, state=state, pid_alive=alive)
    return data

def initialize_resource():
    cfg=config()
    RESOURCE.mkdir(parents=True,exist_ok=True); done_dir().mkdir(parents=True,exist_ok=True)
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
    # Do not silently rebuild a missing plugin index.
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

def clean_stale_runtime():
    """Only called when no worker process exists. Nothing a dead worker left behind may run in the next one."""
    for name in ('bridge-status.json', 'bridge-job.json'):
        (RUNTIME/name).unlink(missing_ok=True)
    done = done_dir(); done.mkdir(parents=True, exist_ok=True)
    moved = {'orphaned': 0, 'cancelled': 0, 'archived': 0, 'pruned': 0}
    for f in list(QUEUE.iterdir()):
        if not f.is_file(): continue
        if f.name.endswith('.running.lua'):
            target = f.name.replace('.running.lua', '.orphaned.lua'); moved['orphaned'] += 1
        elif f.name.endswith('.request.lua'):
            # A request the dead worker never picked up was written for its project, not for a fresh one.
            target = f.name.replace('.request.lua', '.cancelled.lua'); moved['cancelled'] += 1
        elif f.name.endswith(('.done.lua', '.result.json', '.cancelled.lua', '.cancelled')):
            target = f.name; moved['archived'] += 1
        elif f.suffix == '.tmp':
            f.unlink(missing_ok=True); continue
        else: continue
        with contextlib.suppress(OSError): os.replace(f, done/target)
    cutoff = time.time() - RETENTION_DAYS*86400
    for f in list(done.iterdir()):
        with contextlib.suppress(OSError):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(); moved['pruned'] += 1
    return moved

def start(timeout=90, show_window=False):
    with lock():
        s=status()
        if s.get('responsive'): return s
        if s.get('state') in ('busy','stalled'):
            # Do not launch a duplicate when an earlier worker is rendering, loading or waiting in a dialog.
            detail=(f"running job {s.get('job_id')} for {s.get('job_elapsed_seconds',0):.0f}s; wait with job_result"
                    if s['state']=='busy' else 'no heartbeat and no job (loading, modal dialog or hung); inspect it')
            pid=(read_json(RUNTIME/'instance.json') or {}).get('pid')
            raise RuntimeError(f'Worker PID {pid} still exists and is {s["state"]}: {detail}. No second instance was started.')
        ini=initialize_resource()
        require_prepared_plugin_cache()
        cleaned=clean_stale_runtime()
        si=subprocess.STARTUPINFO(); si.dwFlags|=subprocess.STARTF_USESHOWWINDOW; si.wShowWindow=1 if show_window else 0
        args=[config()['target']['executable'],'-newinst','-noactivate','-nosplash','-cfgfile',str(ini),'-new',str(ROOT/'scripts/launch_bridge.lua')]
        p=subprocess.Popen(args,startupinfo=si,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        atomic_json(RUNTIME/'instance.json',{'pid':p.pid,'created':time.time(),'args':args})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            s=status()
            if s.get('responsive'):
                s['cleaned']=cleaned
                return s
            if p.poll() is not None: raise RuntimeError(f'REAPER exited with code {p.returncode}')
            time.sleep(.25)
        raise TimeoutError(f'No API heartbeat after {timeout}s; worker PID {p.pid}. No retry was started.')

def pin_project(project):
    """Pin the project every later request must find active (unless it names its own expected_project).
    Survives a crash on purpose: a restarted worker holds an empty project. Cleared by stop() or pin_project('')."""
    if project: atomic_json(pin_file(), {'project': project})
    else: pin_file().unlink(missing_ok=True)
    return {'pinned_project': project or None}

def _guard(expected_project):
    """One Lua line that refuses to run against another project. None = use the pin, '*' = no check,
    '' = the unsaved (unnamed) project. Checked when the job starts, not while it runs."""
    if expected_project is None:
        expected_project = (read_json(pin_file()) or {}).get('project')
    if expected_project is None or expected_project == '*': return ''
    # Lua long literal delimiter selected to avoid source injection through a path.
    delim='='
    while ']'+delim+']' in expected_project: delim+='='
    lit='['+delim+'['+expected_project+']'+delim+']'
    return ("local _,p=reaper.EnumProjects(-1,''); local function n(s) return (s:gsub('\\\\','/')):lower() end; "
            f"assert(n(p)==n({lit}),'Unexpected active project: '..p)\n")

def _submit(code, expected_project, allow_busy):
    s=status()
    if s['state']=='busy' and not allow_busy:
        raise RuntimeError(f"Worker is busy with job {s.get('job_id')} for {s.get('job_elapsed_seconds',0):.0f}s; "
                           'wait for it with job_result instead of sending the same work again.')
    if not s['responsive'] and s['state']!='busy':
        raise RuntimeError(f"Worker is {s['state']}; start or inspect it first.")
    # Time-ordered ids: the bridge takes the first match of a directory listing, so jobs run in submission order.
    job='%015x'%(time.time_ns()//1000)+uuid.uuid4().hex[:9]
    temp=QUEUE/f'{job}.tmp'
    temp.write_text(_guard(expected_project)+code,encoding='utf-8',newline='\n')
    os.replace(temp,QUEUE/f'{job}.request.lua')
    return job

def _job_state(job):
    done=done_dir()
    for folder in (QUEUE, done):
        path=folder/f'{job}.result.json'
        if path.exists():
            result=read_json(path)
            if result is None: continue
            if folder is QUEUE:
                with contextlib.suppress(OSError): os.replace(path, done/path.name)
            result['job_id']=job; result['state']='done'
            return result
    for suffix,state in (('request','queued'),('running','running'),('cancelled','cancelled')):
        if (QUEUE/f'{job}.{suffix}.lua').exists(): return {'job_id':job,'state':state}
    for suffix in ('cancelled','orphaned'):
        if (done/f'{job}.{suffix}.lua').exists(): return {'job_id':job,'state':suffix}
    return {'job_id':job,'state':'unknown'}

def job_result(job_id, wait_seconds=0):
    """Result of a job by id, waiting up to wait_seconds while it is queued or running.
    state: done (with ok/result/error) / queued / running / cancelled / orphaned (worker died during it) / unknown."""
    if not JOB_ID.fullmatch(job_id): raise ValueError('Invalid job id')
    deadline=time.monotonic()+wait_seconds
    while True:
        r=_job_state(job_id)
        if r['state'] not in ('queued','running') or time.monotonic()>=deadline:
            if r['state'] in ('queued','running'):
                s=status(); r['worker_state']=s['state']
                if s.get('job_id')==job_id: r['job_elapsed_seconds']=s.get('job_elapsed_seconds')
                if r['state']=='running' and not s['pid_alive']: r['state']='orphaned'
            return r
        time.sleep(.1)

def submit_lua(code, expected_project=None):
    """Queue Lua and return at once; fetch the outcome with job_result. For renders longer than a tool timeout."""
    with lock():
        return {'job_id':_submit(code,expected_project,True),'state':'queued'}

def run_lua(code,timeout=60,expected_project=None):
    with lock():
        job=_submit(code,expected_project,False)
        r=job_result(job,timeout)
        if r['state']=='done':
            if not r['ok']: raise RuntimeError(json.dumps(r,ensure_ascii=False))
            return r
        if r['state']=='queued':
            with contextlib.suppress(FileNotFoundError):
                (QUEUE/f'{job}.request.lua').rename(QUEUE/f'{job}.cancelled.lua')
                raise TimeoutError(f'Job {job} was not picked up; cancelled pending request.')
        raise TimeoutError(f'Job {job} is still running after {timeout}s. Do not resubmit; '
                           f'wait for it with job_result("{job}") (MCP: reaper_job_result).')

def list_fx(query=''):
    result=run_lua("local a={}; local i=0; while true do local ok,n,id=reaper.EnumInstalledFX(i); if not ok then break end; a[#a+1]={name=n,identifier=id}; i=i+1 end; return a",expected_project='*')['result']
    if isinstance(result,dict): result=[]
    return [x for x in result if query.lower() in x['name'].lower()]

def stop():
    result=run_lua("local i=0; while true do local p=reaper.EnumProjects(i,''); if not p then break end; assert(reaper.IsProjectDirty(p)==0,'Save every project tab before stopping'); i=i+1 end; REAPER_SUPPORTER_STOP(); return {stopping=true}",expected_project='*')
    # The bridge disappears first; REAPER keeps writing its own files until the process has ended.
    pid=(read_json(RUNTIME/'instance.json') or {}).get('pid')
    deadline=time.monotonic()+30
    while time.monotonic()<deadline and (pid and process_alive(pid) or (RUNTIME/'bridge-status.json').exists()):
        time.sleep(.2)
    result['exited']=not (pid and process_alive(pid))
    if result['exited']: pin_file().unlink(missing_ok=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['status','start','stop','list-fx','run-lua','submit-lua','job-result','pin'])
    parser.add_argument('--file',type=Path); parser.add_argument('--query',default='')
    parser.add_argument('--timeout',type=int,default=60); parser.add_argument('--expected-project')
    parser.add_argument('--job',help='Job id for job-result'); parser.add_argument('--project',default='',help='Project path for pin; empty clears it')
    parser.add_argument('--show-window',action='store_true',help='Show dedicated REAPER for initial setup/dialogs')
    args=parser.parse_args()
    if args.operation=='start': result=start(args.timeout,args.show_window)
    elif args.operation=='status': result=status()
    elif args.operation=='stop': result=stop()
    elif args.operation=='list-fx': result=list_fx(args.query)
    elif args.operation=='pin': result=pin_project(args.project)
    elif args.operation=='job-result':
        if not args.job: parser.error('--job is required')
        result=job_result(args.job,args.timeout)
    else:
        if not args.file: parser.error('--file is required')
        code=args.file.read_text(encoding='utf-8-sig')
        result=(submit_lua(code,args.expected_project) if args.operation=='submit-lua'
                else run_lua(code,args.timeout,args.expected_project))
    print(json.dumps(result,ensure_ascii=False,indent=2))
