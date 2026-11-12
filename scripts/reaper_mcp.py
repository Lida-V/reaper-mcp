"""Supporter-owned stdio MCP adapter over official ReaScript API (not Cockos official MCP)."""
import json
import sys
import reaper_api as api

PROTOCOL_VERSION = '2024-11-05'

class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

def schema(properties=None, required=None):
    return {'type':'object','properties':properties or {},'required':required or [],'additionalProperties':False}

EXPECTED=('expected_project is required: the exact .rpp path the code is meant for (the job fails if another project is active when it starts), '
          '"" for the unsaved new project, or "*" when the code does not depend on the project (opening one, global queries).')

TOOLS=[
 {'name':'reaper_status','description':'Read the isolated REAPER worker state and active project. state: idle / busy (a job is executing; job_id and elapsed seconds are given, and no heartbeat is expected) / stalled (process exists without heartbeat or job: loading, a modal dialog, or hung) / not_started. Does not start REAPER.','inputSchema':schema()},
 {'name':'reaper_start','description':'Start the dedicated REAPER instance using the supporter resource folder; preserves normal streaming configuration. Refuses while an earlier worker process is busy or stalled. Requests a dead worker never ran are cancelled, not replayed.','inputSchema':schema()},
 {'name':'reaper_list_fx','description':'List registered effects through EnumInstalledFX. Listed does not mean load-tested or licensed.','inputSchema':schema({'query':{'type':'string'}})},
 {'name':'reaper_run_lua','description':'Execute trusted Lua through official ReaScript API in the dedicated worker and wait for it; this is NOT a security sandbox. Return a JSON-serializable Lua value. '+EXPECTED+' The result carries the project that was active. On timeout the job keeps running: do not resend it, wait with reaper_job_result. Render with REAPER_SUPPORTER_RENDER({overwrite=true}) after setting the RENDER_* project info: a plain render action stops in an overwrite dialog when the target exists. No GUI interaction.','inputSchema':schema({'code':{'type':'string'},'timeout_seconds':{'type':'integer','minimum':1,'maximum':600},'expected_project':{'type':'string'}},['code','expected_project'])},
 {'name':'reaper_submit_lua','description':'Queue trusted Lua and return its job_id at once, for work longer than a tool timeout (full-song renders). Jobs run one at a time in submission order. '+EXPECTED+' Fetch the outcome with reaper_job_result.','inputSchema':schema({'code':{'type':'string'},'expected_project':{'type':'string'}},['code','expected_project'])},
 {'name':'reaper_job_result','description':'Outcome of a job by job_id, waiting up to wait_seconds while it is queued or running. state: done (ok/result/error) / queued / running / cancelled / orphaned (worker died during it) / unknown.','inputSchema':schema({'job_id':{'type':'string'},'wait_seconds':{'type':'integer','minimum':0,'maximum':600}},['job_id'])},
 {'name':'reaper_pin_project','description':'Pin the project path that later Lua must find active when it names no expected_project of its own (scripts calling the Python API). A restarted worker holds an empty project, so the pin survives a crash. Empty string clears it; reaper_stop clears it too.','inputSchema':schema({'project':{'type':'string'}},['project'])},
 {'name':'reaper_stop','description':'Gracefully stop only the dedicated worker. Refuses while any project tab has unsaved changes.','inputSchema':schema()},
]

def call(name,args):
    tool = next((tool for tool in TOOLS if tool['name'] == name), None)
    if tool is None:
        raise RpcError(-32602, 'Unknown tool')
    if not isinstance(args, dict):
        raise RpcError(-32602, 'Tool arguments must be an object')
    spec = tool['inputSchema']
    if set(args) - set(spec['properties']) or set(spec['required']) - set(args):
        raise RpcError(-32602, 'Unexpected or missing tool arguments')
    for key, value in args.items():
        field = spec['properties'][key]
        if field['type'] == 'string' and not isinstance(value, str):
            raise RpcError(-32602, key + ' must be a string')
        if field['type'] == 'integer' and (type(value) is not int or not field['minimum'] <= value <= field['maximum']):
            raise RpcError(-32602, f"{key} must be an integer from {field['minimum']} to {field['maximum']}")
    if name=='reaper_status': return api.status()
    if name=='reaper_start': return api.start()
    if name=='reaper_list_fx': return api.list_fx(args.get('query',''))
    if name=='reaper_stop': return api.stop()
    if name=='reaper_run_lua':
        return api.run_lua(args['code'],args.get('timeout_seconds',60),args['expected_project'])
    if name=='reaper_submit_lua': return api.submit_lua(args['code'],args['expected_project'])
    if name=='reaper_job_result': return api.job_result(args['job_id'],args.get('wait_seconds',0))
    if name=='reaper_pin_project': return api.pin_project(args['project'])
    raise ValueError('Unknown tool: '+name)

def dispatch(req):
    method=req.get('method'); params=req.get('params',{})
    if not isinstance(params, dict):
        raise RpcError(-32602, 'Params must be an object')
    if method=='initialize':
        return {'protocolVersion':PROTOCOL_VERSION,'capabilities':{'tools':{}},'serverInfo':{'name':'reaper-mcp','version':'0.2.0'},'instructions':'Community adapter using official ReaScript API. Not affiliated with Cockos. Separate REAPER configuration is NOT a security sandbox. Execute only trusted Lua. Never assume plugin GUI-only functions are API accessible.'}
    if method=='ping': return {}
    if method=='tools/list': return {'tools':TOOLS}
    if method=='tools/call':
        if not isinstance(params.get('name'), str):
            raise RpcError(-32602, 'A tool name is required')
        try:
            result=call(params['name'],params.get('arguments',{}))
            return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'isError':False}
        except RpcError:
            raise
        except Exception as e:
            return {'content':[{'type':'text','text':str(e)}],'isError':True}
    raise RpcError(-32601, 'Method not found')

def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        req=None
        try:
            req=json.loads(line)
            if not isinstance(req, dict) or req.get('jsonrpc') != '2.0' or not isinstance(req.get('method'), str):
                raise RpcError(-32600, 'Invalid JSON-RPC request')
            if 'id' not in req: continue
            response={'jsonrpc':'2.0','id':req['id'],'result':dispatch(req)}
        except Exception as e:
            code = -32700 if isinstance(e, json.JSONDecodeError) else e.code if isinstance(e, RpcError) else -32603
            response={'jsonrpc':'2.0','id':req.get('id') if isinstance(req,dict) else None,'error':{'code':code,'message':str(e)}}
        print(json.dumps(response,ensure_ascii=False),flush=True)

if __name__=='__main__': main()
