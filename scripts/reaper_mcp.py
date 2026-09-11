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

TOOLS=[
 {'name':'reaper_status','description':'Read the isolated REAPER worker heartbeat and active project. Does not start REAPER.','inputSchema':schema()},
 {'name':'reaper_start','description':'Start the dedicated REAPER instance using the supporter resource folder; preserves normal streaming configuration.','inputSchema':schema()},
 {'name':'reaper_list_fx','description':'List registered effects through EnumInstalledFX. Listed does not mean load-tested or licensed.','inputSchema':schema({'query':{'type':'string'}})},
 {'name':'reaper_run_lua','description':'Execute trusted Lua through official ReaScript API in the dedicated worker; this is NOT a security sandbox. Return a JSON-serializable Lua value. For edits to an existing project pass its exact expected_project. On timeout inspect job evidence before retrying. No GUI interaction.','inputSchema':schema({'code':{'type':'string'},'timeout_seconds':{'type':'integer','minimum':1,'maximum':600},'expected_project':{'type':'string'}},['code'])},
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
            raise RpcError(-32602, key + ' must be an integer from 1 to 600')
    if name=='reaper_status': return api.status()
    if name=='reaper_start': return api.start()
    if name=='reaper_list_fx': return api.list_fx(args.get('query',''))
    if name=='reaper_stop': return api.stop()
    if name=='reaper_run_lua':
        return api.run_lua(args['code'],args.get('timeout_seconds',60),args.get('expected_project'))
    raise ValueError('Unknown tool: '+name)

def dispatch(req):
    method=req.get('method'); params=req.get('params',{})
    if not isinstance(params, dict):
        raise RpcError(-32602, 'Params must be an object')
    if method=='initialize':
        return {'protocolVersion':PROTOCOL_VERSION,'capabilities':{'tools':{}},'serverInfo':{'name':'reaper-mcp','version':'0.1.0'},'instructions':'Community adapter using official ReaScript API. Not affiliated with Cockos. Separate REAPER configuration is NOT a security sandbox. Execute only trusted Lua. Never assume plugin GUI-only functions are API accessible.'}
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
