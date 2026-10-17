"""desktop_app 的离线测试。

所有后端、HTTP 与 WebView 交互都用替身完成；除了 127.0.0.1 的模拟请求外
不发生任何网络访问，也不加载模型或 pywebview 运行时。
"""
import builtins
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

WORKBENCH = Path(__file__).resolve().parent.parent
if str(WORKBENCH) not in sys.path:
    sys.path.insert(0, str(WORKBENCH))

import desktop_app
import runtime_paths

_UNSET = object()
FAKE_VALUES = {'app_id': 'evidence-workbench', 'version': '1.0.0', 'ok': True}


class FakeProcess:
    """subprocess.Popen 的最小替身，记录生命周期调用。"""

    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.pid = 4242
        self.terminated = 0
        self.killed = 0
        self.wait_calls = []

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated += 1
        if self.exit_code is None:
            self.exit_code = 0

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return 0 if self.exit_code is None else self.exit_code

    def kill(self):
        self.killed += 1
        self.exit_code = -9


class StubbornProcess(FakeProcess):
    """忽略 terminate，只有 kill 后才结束。"""

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.killed:
            return -9
        raise subprocess.TimeoutExpired('desktop_app.py', timeout)


class HttpStub:
    """只应答本机后端路径的 HTTP 替身。"""

    def __init__(self, health=_UNSET, cases=_UNSET, css=_UNSET, mesh=_UNSET):
        self.health = dict(FAKE_VALUES) if health is _UNSET else health
        self.cases = {'ok': True, 'cases': [
            {'id': 'case-1', 'title': '案例一：低剂量CT'},
            {'id': 'case-2', 'title': '案例二：肺结节'},
        ]} if cases is _UNSET else cases
        self.css = b'body { background: #f4f6f5; }\n' if css is _UNSET else css
        self.mesh = {'ok': True, 'mesh': [
            {'term': 'lung nodule', 'ui': 'D008171'},
        ]} if mesh is _UNSET else mesh
        self.calls = []

    def __call__(self, url, method='GET', data=None, timeout=5.0, headers=None):
        self.calls.append({
            'url': url, 'method': str(method).upper(), 'data': data, 'timeout': timeout,
        })
        path = urlsplit(url).path
        status, ctype, body = 404, 'application/json; charset=utf-8', None
        if path == '/api/health':
            status, body = 200, json.dumps(self.health, ensure_ascii=False).encode('utf-8')
        elif path == '/presentation.css':
            status, ctype, body = 200, 'text/css; charset=utf-8', self.css
        elif path == '/api/cases':
            status, body = 200, json.dumps(self.cases, ensure_ascii=False).encode('utf-8')
        elif path == '/api/mesh':
            if method.upper() == 'POST':
                status, body = 200, json.dumps(self.mesh, ensure_ascii=False).encode('utf-8')
            else:
                status, body = 405, json.dumps({'ok': False, 'error': '需要 POST'}).encode('utf-8')
        if body is None:
            body = json.dumps({'ok': False, 'error': 'not found'}).encode('utf-8')
        return status, {'Content-Type': ctype}, body

    @property
    def urls(self):
        return [call['url'] for call in self.calls]


class FakeBackend:
    def __init__(self, raise_on_start=False):
        self.data_dir = Path(os.environ.get('EVIDENCE_WORKBENCH_DATA_DIR', tempfile.gettempdir()))
        self.base_url = 'http://127.0.0.1:54321'
        self.log_path = self.data_dir / 'logs' / 'backend-test.log'
        self.process = None
        self._owned = False
        self.started = 0
        self.closed = 0
        self.raise_on_start = raise_on_start

    def start(self, *args, **kwargs):
        self.started += 1
        if self.raise_on_start:
            raise RuntimeError('backend exploded')
        return self

    def close(self):
        self.closed += 1
        self.process = None
        self._owned = False


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler()


class FakeWindow:
    def __init__(self, probe=None):
        self.events = types.SimpleNamespace(loaded=FakeEvent())
        self.probe = probe if probe is not None else {}
        self.scripts = []
        self.destroyed = 0
        self.title = None
        self.url = None
        self.kwargs = {}

    def evaluate_js(self, script):
        self.scripts.append(script)
        return self.probe

    def destroy(self):
        self.destroyed += 1


class FakeWebview:
    def __init__(self, window, raise_on_start=False):
        self.settings = {}
        self.window = window
        self.raise_on_start = raise_on_start
        self.create_args = None
        self.start_kwargs = None
        self.start_calls = 0

    def create_window(self, title, url=None, **kwargs):
        self.window.title = title
        self.window.url = url
        self.window.kwargs = kwargs
        self.create_args = (title, url, kwargs)
        return self.window

    def start(self, **kwargs):
        self.start_calls += 1
        self.start_kwargs = kwargs
        if self.raise_on_start:
            raise RuntimeError('WebView2 runtime missing')
        self.window.events.loaded.fire()
        callback = kwargs.get('func')
        if callback is not None:
            callback()


def complete_probe(base_url):
    return {
        'ready': 'complete',
        'caseButtons': 2,
        'caseTitles': 2,
        'cssHref': base_url + '/presentation.css',
        'stylesheetHrefs': [base_url + '/presentation.css'],
        'documentTitle': desktop_app.APP_NAME,
        'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) WebView2',
        'innerWidth': 1280,
        'innerHeight': 900,
        'bodyTextLength': 900,
    }


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / 'data'
        self.data_dir.mkdir(parents=True)
        self._old_env = os.environ.get('EVIDENCE_WORKBENCH_DATA_DIR')
        os.environ['EVIDENCE_WORKBENCH_DATA_DIR'] = str(self.data_dir)
        self.addCleanup(self._restore_env)
        self.report = Path(self.temp.name) / 'report.json'

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop('EVIDENCE_WORKBENCH_DATA_DIR', None)
        else:
            os.environ['EVIDENCE_WORKBENCH_DATA_DIR'] = self._old_env

    def read_report(self):
        return json.loads(self.report.read_text(encoding='utf-8'))

    def start_backend(self, process=None, stub=None):
        process = process or FakeProcess()
        stub = stub or HttpStub()
        backend = desktop_app.BackendProcess()
        with mock.patch.object(desktop_app.subprocess, 'Popen', return_value=process) as popen, \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=stub):
            backend.start(timeout=1.0, poll_interval=0.01)
        self.addCleanup(backend.close)
        return backend, process, popen, stub


class BackendProcessTests(WorkspaceCase):
    def test_dev_command_is_fixed_and_never_uses_shell(self):
        backend, process, popen, stub = self.start_backend()
        command = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        expected = [
            sys.executable,
            str(Path(desktop_app.__file__).resolve()),
            '--serve', '--port', str(backend.port),
        ]
        self.assertEqual(command, expected)
        self.assertEqual(command[0], sys.executable)
        self.assertNotIn(command[0].lower(), ('cmd', 'cmd.exe', 'powershell'))
        self.assertIs(kwargs['shell'], False)
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
        self.assertEqual(kwargs['cwd'], str(self.data_dir.resolve()))
        self.assertEqual(kwargs['env']['EVIDENCE_WORKBENCH_DATA_DIR'], str(self.data_dir.resolve()))
        if os.name == 'nt':
            self.assertEqual(kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
        self.assertIsNotNone(kwargs['stdout'])
        self.assertTrue(Path(kwargs['stdout'].name).is_file())
        self.assertEqual(Path(kwargs['stdout'].name).parent,
                         self.data_dir.resolve() / 'logs')
        self.assertNotEqual(backend.port, 0)
        self.assertEqual(backend.base_url, 'http://127.0.0.1:%d' % backend.port)
        self.assertTrue(all(call['url'].startswith('http://127.0.0.1:')
                            for call in stub.calls))

    def test_frozen_command_reuses_same_executable(self):
        frozen_exe = str(Path(self.temp.name) / 'EvidenceWorkbench.exe')
        backend = desktop_app.BackendProcess()
        with mock.patch.object(desktop_app.sys, 'frozen', True, create=True), \
                mock.patch.object(desktop_app.sys, 'executable', frozen_exe), \
                mock.patch.object(desktop_app.subprocess, 'Popen', return_value=FakeProcess()) as popen, \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=HttpStub()):
            backend.start(timeout=1.0, poll_interval=0.01)
        self.addCleanup(backend.close)
        self.assertEqual(
            popen.call_args.args[0],
            [frozen_exe, '--serve', '--port', str(backend.port)],
        )

    def test_health_requires_exact_app_id_and_version(self):
        stub = HttpStub(health={'app_id': 'some-other-app', 'version': runtime_paths.VERSION, 'ok': True})
        process = FakeProcess()
        backend = desktop_app.BackendProcess()
        with mock.patch.object(desktop_app.subprocess, 'Popen', return_value=process), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=stub):
            with self.assertRaises(desktop_app.BackendError) as caught:
                backend.start(timeout=1.0, poll_interval=0.01)
        self.assertIn('身份校验失败', str(caught.exception))
        self.assertIn('some-other-app', str(caught.exception))
        self.assertEqual(process.terminated, 1)
        self.assertIsNone(backend.process)
        self.assertFalse(backend._owned)

    def test_startup_timeout_cleans_owned_child(self):
        process = FakeProcess()
        backend = desktop_app.BackendProcess()
        with mock.patch.object(desktop_app.subprocess, 'Popen', return_value=process), \
                mock.patch.object(desktop_app, '_request_bytes',
                                  side_effect=OSError('connection refused')):
            with self.assertRaises(desktop_app.BackendError) as caught:
                backend.start(timeout=0.05, poll_interval=0.01)
        self.assertIn('超时', str(caught.exception))
        self.assertEqual(process.terminated, 1)
        self.assertEqual(process.wait_calls, [desktop_app.TERMINATE_GRACE_SECONDS])
        self.assertIsNone(backend.process)
        self.assertIsNone(backend._log_handle)
        self.assertTrue(backend.log_path.exists())
        self.assertTrue(self.data_dir.exists())

    def test_early_exit_is_reported_without_touching_exited_process(self):
        process = FakeProcess(exit_code=7)
        backend = desktop_app.BackendProcess()
        with mock.patch.object(desktop_app.subprocess, 'Popen', return_value=process), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=HttpStub()):
            with self.assertRaises(desktop_app.BackendError) as caught:
                backend.start(timeout=1.0, poll_interval=0.01)
        message = str(caught.exception)
        self.assertIn('提前退出', message)
        self.assertIn('7', message)
        self.assertEqual(process.terminated, 0)
        self.assertIsNone(backend.process)
        self.assertFalse(backend._owned)

    def test_close_is_idempotent_and_only_stops_owned_child(self):
        backend, process, popen, stub = self.start_backend()
        backend.close()
        self.assertEqual(process.terminated, 1)
        self.assertEqual(process.wait_calls, [desktop_app.TERMINATE_GRACE_SECONDS])
        backend.close()
        self.assertEqual(process.terminated, 1)
        self.assertEqual(popen.call_count, 1)
        source = Path(desktop_app.__file__).read_text(encoding='utf-8').lower()
        self.assertNotIn('taskkill', source)
        self.assertNotIn('killall', source)

    def test_close_kills_after_terminate_grace(self):
        process = StubbornProcess()
        backend = desktop_app.BackendProcess()
        with mock.patch.object(desktop_app.subprocess, 'Popen', return_value=process), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=HttpStub()):
            backend.start(timeout=1.0, poll_interval=0.01)
        backend.close()
        self.assertEqual(process.terminated, 1)
        self.assertEqual(process.killed, 1)
        self.assertEqual(process.wait_calls,
                         [desktop_app.TERMINATE_GRACE_SECONDS, desktop_app.KILL_GRACE_SECONDS])
        self.assertIsNone(backend.process)

    def test_user_data_is_preserved_and_outputs_stay_in_profile(self):
        backend, process, popen, stub = self.start_backend()
        log_path = backend.log_path
        backend.close()
        self.assertTrue(self.data_dir.exists())
        self.assertTrue(log_path.exists())
        self.assertEqual(runtime_paths.user_data_dir().resolve(), self.data_dir.resolve())
        self.assertEqual(runtime_paths.outputs_dir().resolve(),
                         (self.data_dir / 'outputs').resolve())

    def test_startup_uses_bounded_timeouts(self):
        self.assertLessEqual(desktop_app.BACKEND_START_TIMEOUT, 20.0)
        self.assertLessEqual(desktop_app.SMOKE_TIMEOUT, 30.0)
        self.assertEqual(desktop_app.TERMINATE_GRACE_SECONDS, 3.0)
        self.assertEqual(desktop_app.KILL_GRACE_SECONDS, 3.0)


class HttpGuardTests(WorkspaceCase):
    def test_external_urls_are_rejected_before_any_request(self):
        with mock.patch.object(desktop_app, '_OPENER') as opener:
            for url in ('https://example.com/api/health', 'http://192.168.1.10:8080/x',
                        'http://127.0.0.1.evil.test/x'):
                with self.assertRaises(ValueError):
                    desktop_app._request_bytes(url)
        opener.open.assert_not_called()

    def test_mesh_ui_detection(self):
        value = {'ok': True, 'mesh': [{'term': 'lung nodule', 'ui': 'D008171'}]}
        self.assertIn('D008171', desktop_app._find_mesh_ui(value))
        self.assertIn('D008171',
                      desktop_app._find_mesh_ui({'ok': True, 'note': 'MeSH D008171'}))
        self.assertEqual(desktop_app._find_mesh_ui({'ok': True, 'terms': ['lung nodule']}), [])

    def test_case_extraction_supports_list_and_wrappers(self):
        two = [{'id': 'a'}, {'id': 'b'}]
        self.assertEqual(desktop_app._case_list(two), two)
        self.assertEqual(desktop_app._case_list({'ok': True, 'cases': two}), two)
        self.assertEqual(desktop_app._case_list({'ok': True, 'data': {'items': two}}), two)
        keyed = {'ok': True, 'case-a': {'id': 'a'}, 'case-b': {'id': 'b'}}
        self.assertEqual(desktop_app._case_list(keyed), [{'id': 'a'}, {'id': 'b'}])
        self.assertEqual(desktop_app._case_titles(two), ['a', 'b'])

    def test_smoke_script_contains_expected_probes(self):
        script = desktop_app._smoke_script(['案例一', '案例二'])
        self.assertIn('案例一', script)
        self.assertIn('presentation.css', script)
        self.assertIn('caseButtons', script)
        self.assertIn('navigator.userAgent', script)


class SelfTestTests(WorkspaceCase):
    def run_with(self, stub, backend=None, report=None):
        backend = backend or FakeBackend()
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=stub):
            code = desktop_app.run_self_test(str(report) if report else None)
        return code, backend

    def test_self_test_passes_with_real_http_contract(self):
        stub = HttpStub()
        code, backend = self.run_with(stub, report=self.report)
        self.assertEqual(code, 0)
        report = self.read_report()
        self.assertTrue(report['ok'])
        self.assertEqual(report['app_id'], runtime_paths.APP_ID)
        self.assertEqual(report['version'], runtime_paths.VERSION)
        names = {check['name']: check['ok'] for check in report['checks']}
        self.assertEqual(names, {
            'backend_start': True,
            'backend_health': True,
            'static_css': True,
            'curated_cases': True,
            'mesh_offline': True,
            'backend_stopped': True,
        })
        self.assertEqual(backend.started, 1)
        self.assertEqual(backend.closed, 1)
        self.assertTrue(all(urlsplit(call['url']).hostname == '127.0.0.1'
                            for call in stub.calls))
        self.assertTrue(any(call['method'] == 'POST' and
                            desktop_app.MESH_TEST_PICO in call['data'].decode('utf-8')
                            for call in stub.calls))
        paths = [urlsplit(url).path for url in stub.urls]
        self.assertIn('/presentation.css', paths)
        self.assertIn('/api/cases', paths)
        self.assertIn('/api/mesh', paths)
        self.assertEqual(report['report_path'], str(self.report.resolve()))

    def test_self_test_fails_on_wrong_app_identity(self):
        stub = HttpStub(health={'app_id': 'wrong-app', 'version': '1.0.0', 'ok': True})
        code, backend = self.run_with(stub, report=self.report)
        self.assertEqual(code, 1)
        report = self.read_report()
        self.assertFalse(report['ok'])
        checks = {check['name']: check['ok'] for check in report['checks']}
        self.assertFalse(checks['backend_health'])
        self.assertEqual(backend.closed, 1)

    def test_self_test_requires_two_curated_cases(self):
        stub = HttpStub(cases={'ok': True, 'cases': [{'id': 'only-one'}]})
        code, backend = self.run_with(stub, report=self.report)
        self.assertEqual(code, 1)
        checks = {check['name']: check['ok'] for check in self.read_report()['checks']}
        self.assertFalse(checks['curated_cases'])

    def test_self_test_requires_mesh_ui(self):
        stub = HttpStub(mesh={'ok': True, 'terms': ['lung nodule']})
        code, backend = self.run_with(stub, report=self.report)
        self.assertEqual(code, 1)
        checks = {check['name']: check['ok'] for check in self.read_report()['checks']}
        self.assertFalse(checks['mesh_offline'])

    def test_self_test_start_failure_writes_report_and_stops_child(self):
        backend = FakeBackend(raise_on_start=True)
        code, backend = self.run_with(HttpStub(), backend=backend, report=self.report)
        self.assertEqual(code, 1)
        report = self.read_report()
        self.assertFalse(report['ok'])
        checks = {check['name']: check['ok'] for check in report['checks']}
        self.assertFalse(checks['backend_start'])
        self.assertTrue(checks['backend_stopped'])
        self.assertEqual(backend.closed, 1)
        self.assertIn('启动失败', report['error'])

    def test_self_test_never_imports_webview_or_backend_modules(self):
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name in ('webview', 'http_service', 'orchestrator'):
                raise AssertionError('自检不应导入 %s' % name)
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, '__import__', side_effect=guarded):
            code, backend = self.run_with(HttpStub(), report=self.report)
        self.assertEqual(code, 0)

    def test_self_test_default_report_location_is_user_profile(self):
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=FakeBackend()), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=HttpStub()):
            code = desktop_app.run_self_test()
        self.assertEqual(code, 0)
        default_report = self.data_dir / 'logs' / 'self-test-report.json'
        self.assertTrue(default_report.is_file())
        self.assertTrue(json.loads(default_report.read_text(encoding='utf-8'))['ok'])


class SmokeTestTests(WorkspaceCase):
    def test_hidden_webview2_smoke_test_passes_and_writes_report(self):
        backend = FakeBackend()
        window = FakeWindow(complete_probe(backend.base_url))
        webview = FakeWebview(window)
        stub = HttpStub()
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=stub), \
                mock.patch.dict(sys.modules, {'webview': webview}), \
                mock.patch.object(desktop_app, '_friendly_failure') as friendly:
            code = desktop_app.run_smoke_test(str(self.report), timeout=5.0)
        self.assertEqual(code, 0)
        self.assertFalse(friendly.called)
        report = self.read_report()
        self.assertTrue(report['ok'])
        self.assertEqual(report['kind'], 'smoke-test')
        self.assertEqual(report['title'], desktop_app._app_title())
        self.assertIn(desktop_app.APP_NAME, report['title'])
        self.assertIn(desktop_app.VERSION, report['title'])
        self.assertEqual(report['available_cases'], ['案例一：低剂量CT', '案例二：肺结节'])
        self.assertEqual(report['case_buttons'], 2)
        self.assertEqual(report['window_width'], 1280)
        self.assertIn('WebView2', report['user_agent'])
        self.assertIn('presentation.css', report['css_href'])
        self.assertTrue(all(check['ok'] for check in report['checks']))
        self.assertTrue(all(urlsplit(call['url']).hostname == '127.0.0.1'
                            for call in stub.calls))

        title, url, create_kwargs = webview.create_args
        self.assertEqual(title, desktop_app._app_title())
        self.assertEqual(url, backend.base_url)
        self.assertIs(create_kwargs['hidden'], True)
        self.assertEqual(create_kwargs['width'], 1280)
        self.assertEqual(create_kwargs['height'], 900)
        self.assertEqual(create_kwargs['min_size'], (960, 680))
        self.assertEqual(create_kwargs['background_color'], '#f4f6f5')
        self.assertIs(create_kwargs['text_select'], True)
        self.assertEqual(webview.start_kwargs['gui'], 'edgechromium')
        self.assertIs(webview.start_kwargs['private_mode'], False)
        # Windows TEMP may use an 8.3 alias (RUNNER~1); compare resolved paths.
        self.assertEqual(Path(webview.start_kwargs['storage_path']).resolve(),
                         self.data_dir.resolve() / 'browser')
        self.assertEqual(webview.settings['ALLOW_DOWNLOADS'], True)
        self.assertEqual(webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'], True)
        self.assertGreaterEqual(window.destroyed, 1)
        self.assertEqual(backend.closed, 1)
        self.assertTrue(any('案例一' in script for script in window.scripts))

    def test_smoke_test_times_out_and_destroys_window(self):
        backend = FakeBackend()
        window = FakeWindow({
            'ready': 'loading', 'caseButtons': 0, 'caseTitles': 0,
            'cssHref': '', 'stylesheetHrefs': [],
            'documentTitle': '', 'userAgent': 'WebView2',
            'innerWidth': 1280, 'innerHeight': 900, 'bodyTextLength': 0,
        })
        webview = FakeWebview(window)
        started = time.monotonic()
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=HttpStub()), \
                mock.patch.dict(sys.modules, {'webview': webview}):
            code = desktop_app.run_smoke_test(str(self.report), timeout=0.3)
        elapsed = time.monotonic() - started
        self.assertEqual(code, 1)
        self.assertLess(elapsed, 5.0)
        report = self.read_report()
        self.assertFalse(report['ok'])
        checks = {check['name']: check['ok'] for check in report['checks']}
        self.assertFalse(checks['page_ready'])
        self.assertFalse(checks['case_buttons'])
        self.assertGreaterEqual(window.destroyed, 1)
        self.assertEqual(backend.closed, 1)
        self.assertTrue(report['error'])

    def test_smoke_webview_failure_is_reported_and_server_stopped(self):
        backend = FakeBackend()
        window = FakeWindow(complete_probe(backend.base_url))
        webview = FakeWebview(window, raise_on_start=True)
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.object(desktop_app, '_request_bytes', side_effect=HttpStub()), \
                mock.patch.dict(sys.modules, {'webview': webview}), \
                mock.patch.object(desktop_app, '_friendly_failure') as friendly:
            code = desktop_app.run_smoke_test(str(self.report), timeout=5.0)
        self.assertEqual(code, 1)
        report = self.read_report()
        self.assertFalse(report['ok'])
        self.assertIn('WebView2', report['error'])
        self.assertEqual(webview.start_calls, 1)
        self.assertEqual(webview.start_kwargs['gui'], 'edgechromium')
        self.assertGreaterEqual(window.destroyed, 1)
        self.assertEqual(backend.closed, 1)
        friendly.assert_called_once()


class GuiTests(WorkspaceCase):
    def test_main_window_configuration_and_persistence(self):
        backend = FakeBackend()
        window = FakeWindow()
        webview = FakeWebview(window)
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.dict(sys.modules, {'webview': webview}), \
                mock.patch.object(desktop_app, '_show_messagebox') as box:
            code = desktop_app.main([])
        self.assertEqual(code, 0)
        self.assertFalse(box.called)
        title, url, create_kwargs = webview.create_args
        self.assertEqual(title, desktop_app._app_title())
        self.assertIn(desktop_app.APP_NAME, title)
        self.assertIn(desktop_app.VERSION, title)
        self.assertEqual(url, backend.base_url)
        self.assertEqual(create_kwargs['width'], 1280)
        self.assertEqual(create_kwargs['height'], 900)
        self.assertEqual(create_kwargs['min_size'], (960, 680))
        self.assertEqual(create_kwargs['background_color'], '#f4f6f5')
        self.assertIs(create_kwargs['text_select'], True)
        self.assertFalse(create_kwargs.get('hidden', False))
        self.assertEqual(webview.start_kwargs['gui'], 'edgechromium')
        self.assertIs(webview.start_kwargs['private_mode'], False)
        self.assertEqual(Path(webview.start_kwargs['storage_path']),
                         self.data_dir.resolve() / 'browser')
        self.assertEqual(webview.settings['ALLOW_DOWNLOADS'], True)
        self.assertEqual(webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'], True)
        self.assertEqual(backend.started, 1)
        self.assertEqual(backend.closed, 1)
        # 普通应用不得自行关闭窗口。
        self.assertEqual(window.destroyed, 0)

    def test_webview_failure_shows_message_and_stops_backend(self):
        backend = FakeBackend()
        window = FakeWindow()
        webview = FakeWebview(window, raise_on_start=True)
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.dict(sys.modules, {'webview': webview}), \
                mock.patch.object(desktop_app, '_show_messagebox') as box:
            code = desktop_app.run_app()
        self.assertEqual(code, 1)
        self.assertEqual(backend.closed, 1)
        self.assertGreaterEqual(window.destroyed, 1)
        box.assert_called_once()
        message = box.call_args.args[0].lower()
        self.assertIn('webview2', message)
        self.assertIn('不会自动下载或安装', box.call_args.args[0])
        self.assertEqual(webview.start_calls, 1)
        self.assertTrue((self.data_dir / 'logs' / 'desktop-error.log').is_file())

    def test_missing_pywebview_is_a_friendly_failure(self):
        backend = FakeBackend()
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.dict(sys.modules, {'webview': None}), \
                mock.patch.object(desktop_app, '_show_messagebox') as box:
            code = desktop_app.run_app()
        self.assertEqual(code, 1)
        self.assertEqual(backend.closed, 1)
        box.assert_called_once()
        self.assertTrue((self.data_dir / 'logs' / 'desktop-error.log').is_file())

    def test_backend_failure_never_opens_a_window(self):
        backend = FakeBackend(raise_on_start=True)
        webview = FakeWebview(FakeWindow())
        with mock.patch.object(desktop_app, 'BackendProcess', return_value=backend), \
                mock.patch.dict(sys.modules, {'webview': webview}), \
                mock.patch.object(desktop_app, '_show_messagebox') as box:
            code = desktop_app.run_app()
        self.assertEqual(code, 1)
        self.assertIsNone(webview.create_args)
        self.assertEqual(backend.closed, 1)
        box.assert_called_once()


class CliTests(WorkspaceCase):
    def test_serve_flag_uses_internal_backend_and_port(self):
        with mock.patch.object(desktop_app, '_run_backend', return_value=0) as run:
            code = desktop_app.main(['--serve', '--port', '8123'])
        self.assertEqual(code, 0)
        run.assert_called_once_with(8123)

    def test_serve_default_port(self):
        with mock.patch.object(desktop_app, '_run_backend', return_value=0) as run:
            desktop_app.main(['--serve'])
        run.assert_called_once_with(desktop_app.DEFAULT_BACKEND_PORT)

    def test_self_and_smoke_flags_route_reports(self):
        with mock.patch.object(desktop_app, 'run_self_test', return_value=4) as selftest:
            self.assertEqual(desktop_app.main(['--self-test', '--report', 'a.json']), 4)
            selftest.assert_called_once_with('a.json')
        with mock.patch.object(desktop_app, 'run_smoke_test', return_value=5) as smoke:
            self.assertEqual(desktop_app.main(['--smoke-test', '--report', 'b.json']), 5)
            smoke.assert_called_once_with('b.json')

    def test_ordinary_main_opens_gui(self):
        with mock.patch.object(desktop_app, 'run_app', return_value=0) as run:
            self.assertEqual(desktop_app.main([]), 0)
        run.assert_called_once_with()

    def test_internal_backend_binds_loopback_only_and_never_imports_webview(self):
        server = mock.MagicMock()
        fake_service = types.ModuleType('http_service')
        fake_service.Handler = object()
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == 'webview':
                raise AssertionError('后端模式不应导入 webview')
            return real_import(name, *args, **kwargs)

        with mock.patch.dict(sys.modules, {'http_service': fake_service}), \
                mock.patch('http.server.ThreadingHTTPServer', return_value=server) as ctor, \
                mock.patch.object(builtins, '__import__', side_effect=guarded), \
                contextlib.redirect_stdout(io.StringIO()):
            code = desktop_app._run_backend(12345, data_dir=self.data_dir)
        self.assertEqual(code, 0)
        ctor.assert_called_once_with(('127.0.0.1', 12345), fake_service.Handler)
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()
        self.assertEqual(os.environ['EVIDENCE_WORKBENCH_DATA_DIR'], str(self.data_dir))

    def test_internal_backend_rejects_invalid_ports(self):
        for port in (0, -1, 70000):
            with self.assertRaises(desktop_app.BackendError):
                desktop_app._run_backend(port, data_dir=self.data_dir)


class ImportContractTests(WorkspaceCase):
    def test_runtime_paths_public_contract(self):
        self.assertEqual(runtime_paths.VERSION, '1.0.0')
        self.assertEqual(runtime_paths.APP_ID, 'evidence-workbench')
        self.assertTrue(runtime_paths.APP_NAME)
        self.assertEqual(runtime_paths.workbench_root().name, '循证工作台')
        self.assertEqual(runtime_paths.user_data_dir().resolve(), self.data_dir.resolve())
        self.assertEqual(runtime_paths.outputs_dir().resolve(),
                         (self.data_dir / 'outputs').resolve())

    def test_importing_desktop_app_does_not_import_backend_or_webview(self):
        code = (
            'import sys; sys.path.insert(0, %r); import desktop_app; '
            'print(int("http_service" in sys.modules), int("orchestrator" in sys.modules), '
            'int("webview" in sys.modules), desktop_app.APP_ID, desktop_app.VERSION)'
        ) % str(WORKBENCH)
        result = subprocess.run(
            [sys.executable, '-I', '-c', code],
            cwd=str(WORKBENCH), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        fields = result.stdout.strip().split()
        self.assertEqual(fields, ['0', '0', '0', 'evidence-workbench', '1.0.0'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
