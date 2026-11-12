import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import reaper_api as api
import reaper_mcp as mcp


class ProtocolTests(unittest.TestCase):
    def test_stdio_recovers_after_invalid_requests(self):
        messages = [
            '{bad json',
            '[]',
            json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
            json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                        'params': {'protocolVersion': '2099-01-01'}}),
            json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}),
            json.dumps({'jsonrpc': '2.0', 'id': 3, 'method': 'unsupported'}),
            json.dumps({'jsonrpc': '2.0', 'id': 4, 'method': 'ping'}),
        ]
        run = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/reaper_mcp.py')],
                             input='\n'.join(messages) + '\n', capture_output=True,
                             text=True, encoding='utf-8', timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, '')
        replies = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(replies), 6)
        self.assertEqual(replies[0]['error']['code'], -32700)
        self.assertEqual(replies[1]['error']['code'], -32600)
        self.assertEqual(replies[2]['result']['protocolVersion'], '2024-11-05')
        self.assertEqual(len(replies[3]['result']['tools']), 8)
        self.assertEqual(replies[4]['error']['code'], -32601)
        self.assertEqual(replies[5]['result'], {})

    def test_bad_arguments_never_reach_reaper(self):
        ok = {'code': 'return true', 'expected_project': '*'}
        invalid = [
            {}, {'code': 42, 'expected_project': '*'}, {**ok, 'timeout_seconds': True},
            {**ok, 'timeout_seconds': 601},
            {**ok, 'timeout_seconds': 0},
            {'code': 'return true', 'expected_project': 123},
            {'code': 'return true'},
            {**ok, 'unexpected': 'x'}, [],
        ]
        with patch.object(api, 'run_lua') as run:
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(mcp.RpcError):
                    mcp.call('reaper_run_lua', args)
            run.assert_not_called()
        with patch.object(api, 'job_result') as result:
            for args in [{}, {'job_id': 7}, {'job_id': 'abcdef01', 'wait_seconds': -1},
                         {'job_id': 'abcdef01', 'wait_seconds': 601}]:
                with self.subTest(args=args), self.assertRaises(mcp.RpcError):
                    mcp.call('reaper_job_result', args)
            result.assert_not_called()

    def test_execution_error_is_tool_error(self):
        with patch.object(api, 'run_lua', side_effect=RuntimeError('Unexpected active project')):
            result = mcp.dispatch({'method': 'tools/call', 'params': {
                'name': 'reaper_run_lua', 'arguments': {'code': 'return true', 'expected_project': 'C:/a.rpp'}}})
        self.assertTrue(result['isError'])
        self.assertIn('Unexpected active project', result['content'][0]['text'])


class WorkerStateTests(unittest.TestCase):
    """No REAPER: the runtime folder is synthetic and the process check is patched."""

    def runtime(self, directory, alive):
        root = Path(directory)
        (root / 'queue' / 'done').mkdir(parents=True)
        (root / 'instance.json').write_text('{"pid": 4242}')
        return [patch.object(api, 'RUNTIME', root), patch.object(api, 'QUEUE', root / 'queue'),
                patch.object(api, 'process_alive', return_value=alive)], root

    def run_with(self, patches, body):
        for p in patches: p.start()
        try: return body()
        finally:
            for p in reversed(patches): p.stop()

    def test_busy_is_not_stopped(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            patches, root = self.runtime(directory, alive=True)
            # A long job writes no heartbeat: the old heartbeat plus the job marker means busy, not stopped.
            (root / 'bridge-status.json').write_text(json.dumps({'updated': time.time() - 120}))
            (root / 'bridge-job.json').write_text(json.dumps({'job': 'abcdef0123', 'started': time.time() - 100}))
            state = self.run_with(patches, api.status)
            self.assertEqual((state['state'], state['responsive'], state['job_id']), ('busy', False, 'abcdef0123'))
            with patch.object(api.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, 'No second instance'):
                    self.run_with(patches, api.start)
                with self.assertRaisesRegex(RuntimeError, 'busy'):
                    self.run_with(patches, lambda: api.run_lua('return 1', 1))
            launch.assert_not_called()
            self.assertEqual(list((root / 'queue').glob('*.lua')), [])

    def test_stalled_and_dead_workers(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            patches, root = self.runtime(directory, alive=True)
            (root / 'bridge-status.json').write_text(json.dumps({'updated': time.time() - 120}))
            self.assertEqual(self.run_with(patches, api.status)['state'], 'stalled')
        with tempfile.TemporaryDirectory() as directory:
            patches, root = self.runtime(directory, alive=False)
            (root / 'bridge-status.json').write_text(json.dumps({'updated': time.time() - 120}))
            (root / 'bridge-job.json').write_text(json.dumps({'job': 'abcdef0123', 'started': 1}))
            self.assertEqual(self.run_with(patches, api.status)['state'], 'not_started')

    def test_leftovers_of_a_dead_worker_never_run(self):
        with tempfile.TemporaryDirectory() as directory:
            patches, root = self.runtime(directory, alive=False)
            queue = root / 'queue'
            (queue / 'aaaaaaaa01.request.lua').write_text('return 1')
            (queue / 'aaaaaaaa02.running.lua').write_text('return 1')
            (queue / 'aaaaaaaa03.result.json').write_text('{"ok": true, "result": 3}')
            (root / 'bridge-job.json').write_text('{"job": "aaaaaaaa02", "started": 1}')
            def body():
                moved = api.clean_stale_runtime()
                return moved, [api.job_result(i)['state'] for i in ('aaaaaaaa01', 'aaaaaaaa02', 'aaaaaaaa03', 'aaaaaaaa04')]
            moved, states = self.run_with(patches, body)
            self.assertEqual((moved['cancelled'], moved['orphaned'], moved['archived']), (1, 1, 1))
            self.assertEqual(states, ['cancelled', 'orphaned', 'done', 'unknown'])
            self.assertEqual([f for f in queue.iterdir() if f.is_file()], [])
            self.assertFalse((root / 'bridge-job.json').exists())

    def test_project_guard_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            patches, root = self.runtime(directory, alive=False)
            def body():
                none = api._guard(None)
                api.pin_project('C:/music/Song.rpp')
                pinned = api._guard(None)
                return none, pinned, api._guard('*'), api._guard(''), api._guard('C:/x]=]y.rpp')
            none, pinned, star, unnamed, tricky = self.run_with(patches, body)
            self.assertEqual((none, star), ('', ''))
            self.assertIn('[=[C:/music/Song.rpp]=]', pinned)
            self.assertIn('n([=[]=])', unnamed)
            self.assertIn('[==[C:/x]=]y.rpp]==]', tricky)
            self.assertEqual(pinned.count('\n'), 1)

    def test_job_id_is_validated_and_ordered(self):
        with self.assertRaises(ValueError):
            api.job_result('../../etc')
        with tempfile.TemporaryDirectory() as directory:
            patches, root = self.runtime(directory, alive=True)
            import time
            (root / 'bridge-status.json').write_text(json.dumps({'updated': time.time()}))
            ids = self.run_with(patches, lambda: [api.submit_lua('return 1', '*')['job_id'] for _ in range(5)])
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(len(list((root / 'queue').glob('*.request.lua'))), 5)


class IsolationTests(unittest.TestCase):
    def test_startup_scan_disabled_without_changing_other_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = root / 'resource'
            resource.mkdir()
            ini = resource / 'reaper.ini'
            ini.write_text('[REAPER]\nvst_scan=2\nvstpath64=C:/Plugins\nother=keep\n[plugin]\nvst_scan=2\n', encoding='utf-8')
            with patch.object(api, 'RESOURCE', resource), patch.object(api, 'QUEUE', root / 'queue'), \
                    patch.object(api, 'config', return_value={'target': {}}):
                api.initialize_resource()
            self.assertEqual(ini.read_text(), '[REAPER]\nvst_scan=1\nvstpath64=C:/Plugins\nother=keep\n[plugin]\nvst_scan=2\n')

    def test_unprepared_profile_cannot_launch_reaper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = root / 'resource'
            resource.mkdir()
            (resource / 'reaper-vstplugins64.ini').write_text('[vstcache]\n')
            with patch.object(api, 'RESOURCE', resource), patch.object(api, 'RUNTIME', root), \
                    patch.object(api, 'status', return_value={'responsive': False, 'state': 'not_started'}), \
                    patch.object(api, 'initialize_resource', return_value=resource / 'reaper.ini'), \
                    patch.object(api.subprocess, 'Popen') as launch, self.assertRaisesRegex(RuntimeError, 'prepared FX index'):
                api.start()
            launch.assert_not_called()

    def test_ini_edit_preserves_non_utf8_path_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            ini = Path(directory) / 'reaper.ini'
            original = b'[REAPER]\r\nvst_scan=2\r\npath=' + 'テスト'.encode('cp932') + b'\r\n'
            ini.write_bytes(original)
            api.set_reaper_ini_options(ini, {'vst_scan': '1'})
            self.assertEqual(ini.read_bytes(), original.replace(b'vst_scan=2', b'vst_scan=1'))

    def test_only_opted_in_fx_cache_is_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            for name in ['reaper-vstplugins64.ini', 'reaper-license.rk', 'reaper-reginfo2.ini', 'reaper.ini']:
                (source / name).write_text('synthetic test fixture', encoding='utf-8')
            resource = root / 'isolated'
            with patch.object(api, 'RESOURCE', resource), patch.object(api, 'QUEUE', root / 'queue'), \
                    patch.object(api, 'config', return_value={'target': {'pluginCacheSource': str(source)}}):
                api.initialize_resource()
            self.assertTrue((resource / 'reaper-vstplugins64.ini').exists())
            self.assertFalse((resource / 'reaper-license.rk').exists())
            self.assertFalse((resource / 'reaper-reginfo2.ini').exists())
            self.assertIn('loadlastproj=0', (resource / 'reaper.ini').read_text())
            self.assertEqual((source / 'reaper.ini').read_text(), 'synthetic test fixture')

    def test_configuration_is_not_overwritten(self):
        import configure
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            config = root / 'config/local.json'
            config.write_text('{"keep": true}', encoding='utf-8')
            (root / 'reaper-vstplugins64.ini').write_text('[vstcache]\nsynthetic.dll=0,1,Test\n')
            with patch.object(configure, 'ROOT', root), \
                    patch.object(sys, 'argv', ['configure.py', '--reaper', sys.executable, '--plugin-cache-source', str(root)]), \
                    patch('sys.stderr'), self.assertRaises(SystemExit):
                configure.main()
            self.assertEqual(config.read_text(), '{"keep": true}')


if __name__ == '__main__':
    unittest.main()
