"""循证科研工作台桌面入口。

本文件是打包后的唯一入口：普通启动打开原生窗口，`--serve` 作为本机后端
子进程运行，`--self-test` / `--smoke-test` 供构建流水线做无模型调用的验证。

设计约束：
* 顶层只导入标准库与本目录的 runtime_paths。pywebview 与 http_service 均延迟
  导入，保证自检不需要 GUI 依赖，也保证 EVIDENCE_WORKBENCH_DATA_DIR 在
  后端模块导入之前设置。
* 父进程只启动自己的可执行文件（开发环境为当前 Python + 本文件），不调用
  系统程序，不经过 shell。
* 只访问 127.0.0.1 上的本机服务。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

try:
    import runtime_paths
except ImportError:  # 以脚本方式被外部测试导入时兜底
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_paths

APP_ID = runtime_paths.APP_ID
APP_NAME = runtime_paths.APP_NAME
VERSION = runtime_paths.VERSION

BACKEND_START_TIMEOUT = 20.0
SMOKE_TIMEOUT = 30.0
TERMINATE_GRACE_SECONDS = 3.0
KILL_GRACE_SECONDS = 3.0
HEALTH_POLL_INTERVAL = 0.15
DEFAULT_BACKEND_PORT = 8130
MAX_HTTP_BYTES = 4 * 1024 * 1024
MESH_TEST_PICO = '低剂量CT肺结节跨机型泛化'
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 900
WINDOW_MIN_SIZE = (960, 680)
WINDOW_BACKGROUND = '#f4f6f5'

_LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}
_MESH_UI_PATTERN = re.compile(r'\b[A-Z]\d{6,9}\b')
_STDIO_HANDLES = []


class BackendError(RuntimeError):
    """本机后端启动、身份校验或停止失败。"""


# ---------------------------------------------------------------------------
# 路径、日志与报告
# ---------------------------------------------------------------------------

def _data_dir():
    return Path(runtime_paths.user_data_dir())


def _log_dir(data_dir=None):
    path = Path(data_dir) if data_dir else _data_dir()
    directory = path / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _append_log(name, message, data_dir=None):
    """尽力写入用户目录日志；窗口化运行时标准流可能不存在。"""
    try:
        path = _log_dir(data_dir) / name
        with path.open('a', encoding='utf-8') as handle:
            handle.write('[%s] %s\n' % (_timestamp(), str(message).rstrip()))
        return path
    except OSError:
        return None


def _write_report(report_path, report, default_name):
    if report_path:
        path = Path(report_path).expanduser()
    else:
        path = _log_dir() / default_name
    path.parent.mkdir(parents=True, exist_ok=True)
    report['report_path'] = str(path.resolve())
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        handle.write(payload)
    os.replace(temporary, path)
    return path


def _show_messagebox(text):
    """仅 Windows 弹出错误提示；不下载、不运行任何安装程序。"""
    if os.name != 'nt':
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(text), '%s %s' % (APP_NAME, VERSION), 0x10)
    except Exception:
        pass


def _friendly_failure(summary, detail, data_dir=None, show_messagebox=False):
    log_path = _append_log('desktop-error.log', '%s\n%s' % (summary, detail), data_dir)
    text = summary
    if detail:
        text += '\n\n' + str(detail)
    if log_path:
        text += '\n\n日志：%s' % log_path
    if show_messagebox:
        _show_messagebox(text)
    return log_path


def _app_title():
    return '%s %s' % (APP_NAME, VERSION)


def _browser_storage_path(data_dir=None):
    """WebView2 的持久化目录必须位于用户数据目录内。"""
    directory = Path(data_dir or _data_dir()) / 'browser'
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


# ---------------------------------------------------------------------------
# 仅本机 HTTP
# ---------------------------------------------------------------------------

class _LoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_loopback_url(newurl):
            raise urllib.error.URLError('拒绝重定向到本机以外的地址')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _LoopbackRedirectHandler()
)


def _is_loopback_url(url):
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return False
    return parts.scheme == 'http' and (parts.hostname or '').lower() in _LOOPBACK_HOSTS


def _request_bytes(url, method='GET', data=None, timeout=5.0, headers=None):
    """访问本机后端；非回环地址直接拒绝，系统代理被显式禁用。"""
    if not _is_loopback_url(url):
        raise ValueError('只允许访问本机后端服务：%s' % url)
    if isinstance(data, str):
        data = data.encode('utf-8')
    request = urllib.request.Request(url, data=data, method=str(method).upper())
    request.add_header('Accept', 'application/json, text/css, */*')
    if data is not None:
        request.add_header('Content-Type', 'application/json; charset=utf-8')
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            if not _is_loopback_url(response.geturl()):
                raise ValueError('后端重定向到了非本机地址')
            status = int(getattr(response, 'status', 0) or response.getcode())
            return status, dict(response.headers), response.read(MAX_HTTP_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(MAX_HTTP_BYTES)
        except Exception:
            raw = b''
        return int(exc.code), dict(exc.headers or {}), raw


def _header(headers, name):
    wanted = str(name).lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return str(value)
    return ''


def _decode_json(raw):
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError('后端返回了无法解析的 JSON') from exc


# ---------------------------------------------------------------------------
# 本机后端进程
# ---------------------------------------------------------------------------

class BackendProcess:
    """父进程拥有的本机后端子进程。

    端口由操作系统分配并只绑定 127.0.0.1。子进程使用与应用相同的可执行文件，
    开发环境则是 `sys.executable desktop_app.py --serve --port N`，不经过 shell。
    """

    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir).expanduser().resolve() if data_dir else _data_dir()
        self.port = None
        self.base_url = None
        self.process = None
        self.log_path = None
        self._log_handle = None
        self._owned = False

    @staticmethod
    def pick_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            return int(sock.getsockname()[1])

    def command(self):
        if self.port is None:
            raise BackendError('尚未分配本机后端端口')
        if getattr(sys, 'frozen', False):
            return [sys.executable, '--serve', '--port', str(self.port)]
        return [sys.executable, str(Path(__file__).resolve()), '--serve', '--port', str(self.port)]

    def start(self, timeout=BACKEND_START_TIMEOUT, poll_interval=HEALTH_POLL_INTERVAL):
        if self.process is not None:
            return self
        timeout = BACKEND_START_TIMEOUT if timeout is None else float(timeout)
        poll_interval = HEALTH_POLL_INTERVAL if poll_interval is None else max(0.0, float(poll_interval))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.port = self.pick_port()
        self.base_url = 'http://127.0.0.1:%d' % self.port
        self.log_path = _log_dir(self.data_dir) / (
            'backend-%s-%d.log' % (datetime.now().strftime('%Y%m%d-%H%M%S'), self.port)
        )
        environment = os.environ.copy()
        environment['EVIDENCE_WORKBENCH_DATA_DIR'] = str(self.data_dir)
        popen_kwargs = {
            'shell': False,
            'cwd': str(self.data_dir),
            'env': environment,
            'stdin': subprocess.DEVNULL,
            'stdout': None,
            'stderr': subprocess.STDOUT,
        }
        if os.name == 'nt':
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        command = self.command()
        try:
            self._log_handle = self.log_path.open('ab', buffering=0)
            popen_kwargs['stdout'] = self._log_handle
            self.process = subprocess.Popen(command, **popen_kwargs)
            self._owned = True
            self._wait_for_health(timeout=timeout, poll_interval=poll_interval)
        except BackendError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise BackendError('无法启动本地服务：%s' % exc) from exc
        except Exception:
            self.close()
            raise
        return self

    def _wait_for_health(self, timeout, poll_interval):
        deadline = time.monotonic() + max(0.0, timeout)
        health_url = self.base_url + '/api/health'
        last_problem = '尚未收到健康响应'
        while True:
            if self.process is not None:
                exit_code = self.process.poll()
                if exit_code is not None:
                    raise BackendError(
                        '本地服务进程提前退出（退出码 %s）。请查看日志目录：%s'
                        % (exit_code, self.log_path.parent)
                    )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                status, _, raw = _request_bytes(
                    health_url, timeout=min(2.0, max(0.2, remaining))
                )
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last_problem = '%s: %s' % (type(exc).__name__, exc)
            else:
                if status != 200:
                    last_problem = '健康检查返回 HTTP %s' % status
                else:
                    try:
                        payload = _decode_json(raw)
                    except ValueError as exc:
                        last_problem = str(exc)
                    else:
                        if not isinstance(payload, dict):
                            last_problem = '健康检查返回的数据格式无效'
                        elif payload.get('app_id') != APP_ID or str(payload.get('version')) != VERSION:
                            raise BackendError(
                                '本地服务身份校验失败（app_id=%r, version=%r）。日志：%s'
                                % (payload.get('app_id'), payload.get('version'), self.log_path)
                            )
                        elif payload.get('ok') is True:
                            return payload
                        else:
                            last_problem = '本地服务尚未就绪（ok=%r）' % payload.get('ok')
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        raise BackendError(
            '本地服务启动超时（最多 %.0f 秒）：%s。日志：%s'
            % (timeout, last_problem, self.log_path)
        )

    def close(self):
        """只停止本实例拥有的子进程；先 terminate，3 秒后 kill。"""
        process = self.process
        owned = self._owned
        self.process = None
        self._owned = False
        if process is not None and owned:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=TERMINATE_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        try:
                            process.wait(timeout=KILL_GRACE_SECONDS)
                        except subprocess.TimeoutExpired:
                            pass
            except Exception:
                pass
        handle = self._log_handle
        self._log_handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    stop = close

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


# ---------------------------------------------------------------------------
# 后端子进程入口
# ---------------------------------------------------------------------------

def _ensure_standard_streams(data_dir=None):
    """窗口化 PyInstaller 中 sys.stdout/stderr 为 None，重定向到用户目录日志。"""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    try:
        path = _log_dir(data_dir) / ('backend-stdio-%d.log' % os.getpid())
        handle = path.open('a', encoding='utf-8', buffering=1, errors='backslashreplace')
    except OSError:
        return None
    _STDIO_HANDLES.append(handle)
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle
    return path


def _run_backend(port, data_dir=None):
    """内部后端：绑定 127.0.0.1 并 serve_forever，绝不启动 GUI。"""
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise BackendError('内部后端端口无效：%r' % (port,)) from exc
    if not 1 <= port <= 65535:
        raise BackendError('内部后端端口超出范围：%d' % port)
    directory = Path(data_dir) if data_dir else _data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # http_service 会导入 orchestrator 等模块，必须在导入前确定数据目录。
    os.environ['EVIDENCE_WORKBENCH_DATA_DIR'] = str(directory)
    _ensure_standard_streams(directory)
    workbench = runtime_paths.workbench_root()
    if workbench.is_dir() and str(workbench) not in sys.path:
        sys.path.append(str(workbench))
    from http.server import ThreadingHTTPServer
    import http_service

    server = ThreadingHTTPServer(('127.0.0.1', port), http_service.Handler)
    try:
        print('%s %s 本机服务：http://127.0.0.1:%d/' % (APP_NAME, VERSION, port))
        print('仅本机单用户访问；未联网时不显示核验通过。')
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# 自检（真实本机 HTTP，无 GUI）
# ---------------------------------------------------------------------------

def _case_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ('cases', 'showcase', 'items', 'results', 'records', 'entries',
                    'examples', 'data'):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _case_list(value)
                if nested:
                    return nested
        dict_values = [value for value in payload.values() if isinstance(value, dict)]
        if len(dict_values) >= 2:
            return dict_values
        for value in payload.values():
            if isinstance(value, list) and value and all(
                    isinstance(item, (dict, str)) for item in value):
                return value
    return []


def _case_titles(cases):
    titles = []
    for case in cases:
        title = ''
        if isinstance(case, str):
            title = case
        elif isinstance(case, dict):
            for key in ('title', 'name', 'case_title', 'caseTitle', 'label', 'id', 'case_id'):
                value = case.get(key)
                if isinstance(value, str) and value.strip():
                    title = value.strip()
                    break
        if title and title not in titles:
            titles.append(title)
    return titles


def _find_mesh_ui(value):
    found = []

    def walk(node, key=''):
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, str(child_key))
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, str):
            normalized = key.lower().replace('-', '').replace('_', '')
            if normalized in ('ui', 'meshui', 'meshid', 'descriptorui', 'descriptorid',
                              'meshtermui', 'conceptui', 'supplementaryconceptui'):
                if node.strip():
                    found.append(node.strip())
            found.extend(match.group(0) for match in _MESH_UI_PATTERN.finditer(node))

    walk(value)
    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def _self_test_api_checks(backend):
    checks = []

    def add(name, ok, detail):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    # 1. 健康检查必须来自预期的应用与版本。
    status = 0
    try:
        status, headers, raw = _request_bytes(backend.base_url + '/api/health', timeout=5.0)
        health = _decode_json(raw)
        healthy = (
            status == 200
            and isinstance(health, dict)
            and health.get('app_id') == APP_ID
            and str(health.get('version')) == VERSION
            and health.get('ok') is True
        )
        add('backend_health', healthy, {
            'status': status,
            'app_id': health.get('app_id') if isinstance(health, dict) else None,
            'version': health.get('version') if isinstance(health, dict) else None,
            'ok': health.get('ok') if isinstance(health, dict) else None,
        })
    except Exception as exc:  # 自检需要把失败写入报告而不是抛出
        add('backend_health', False, {'status': status, 'error': '%s: %s' % (type(exc).__name__, exc)})

    # 2. 静态样式表可被本机服务读取。
    try:
        status, headers, css = _request_bytes(backend.base_url + '/presentation.css', timeout=5.0)
        content_type = _header(headers, 'Content-Type').lower()
        css_ok = status == 200 and len(css) > 0 and 'text/css' in content_type
        add('static_css', css_ok, {
            'status': status,
            'bytes': len(css),
            'content_type': content_type,
        })
    except Exception as exc:
        add('static_css', False, {'error': '%s: %s' % (type(exc).__name__, exc)})

    # 3. 打包内应有两个精选案例。
    try:
        status, headers, raw = _request_bytes(backend.base_url + '/api/cases', timeout=5.0)
        payload = _decode_json(raw)
        cases = _case_list(payload)
        case_ok = status == 200 and len(cases) == 2
        add('curated_cases', case_ok, {
            'status': status,
            'count': len(cases),
            'titles': _case_titles(cases)[:5],
        })
    except Exception as exc:
        add('curated_cases', False, {'error': '%s: %s' % (type(exc).__name__, exc)})

    # 4. 离线 MeSH 请求（仅本机 HTTP，不调用模型或外部网络）。
    try:
        body = json.dumps({'pico': MESH_TEST_PICO}, ensure_ascii=False).encode('utf-8')
        status, headers, raw = _request_bytes(
            backend.base_url + '/api/mesh', method='POST', data=body, timeout=10.0
        )
        payload = _decode_json(raw)
        ui_values = _find_mesh_ui(payload)
        mesh_ok = (
            status == 200
            and isinstance(payload, dict)
            and payload.get('ok') is not False
            and bool(ui_values)
        )
        add('mesh_offline', mesh_ok, {
            'status': status,
            'pico': MESH_TEST_PICO,
            'mesh_ui': ui_values[:5],
        })
    except Exception as exc:
        add('mesh_offline', False, {'error': '%s: %s' % (type(exc).__name__, exc)})

    return checks


def run_self_test(report_path=None):
    started = time.monotonic()
    started_at = _timestamp()
    backend = BackendProcess()
    checks = []
    error = None
    try:
        try:
            backend.start()
        except Exception as exc:
            error = '本地服务启动失败：%s' % exc
            checks.append({'name': 'backend_start', 'ok': False, 'detail': str(exc)})
        else:
            checks.append({'name': 'backend_start', 'ok': True, 'detail': {
                'base_url': backend.base_url,
                'log': str(backend.log_path),
            }})
            checks.extend(_self_test_api_checks(backend))
    finally:
        try:
            backend.close()
        except Exception as exc:
            if error is None:
                error = '本地服务停止失败：%s' % exc
        checks.append({
            'name': 'backend_stopped',
            'ok': backend.process is None and not backend._owned,
            'detail': {'owned_child_released': backend.process is None and not backend._owned},
        })

    all_ok = error is None and bool(checks) and all(check['ok'] for check in checks)
    report = {
        'ok': all_ok,
        'kind': 'self-test',
        'app_id': APP_ID,
        'app_name': APP_NAME,
        'version': VERSION,
        'started_at': started_at,
        'duration_seconds': round(time.monotonic() - started, 3),
        'checks': checks,
        'backend_log': str(backend.log_path) if backend.log_path else None,
        'error': error,
    }
    try:
        _write_report(report_path, report, 'self-test-report.json')
    except Exception as exc:
        _friendly_failure('无法写入自检报告', exc, backend.data_dir, show_messagebox=False)
        return 1
    if not all_ok:
        _friendly_failure('自检未通过', error or '存在失败检查项', backend.data_dir, show_messagebox=False)
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# 冒烟测试（隐藏的真实 WebView2 窗口）
# ---------------------------------------------------------------------------

_SMOKE_JS = r"""
(function () {
  var titles = __TITLES__;
  function textOf(el) { return String((el && (el.innerText || el.textContent)) || '').trim(); }
  var candidates = [];
  function push(el) { if (el && candidates.indexOf(el) < 0) { candidates.push(el); } }
  var selectors = ['.case-button', '.case-card', '.case-item', '.case-row', '.case-list',
    'button[data-case-id]', '[data-case-id]', '[data-case]',
    '#cases button', '#case-list button', '#caseButtons button', '.cases button',
    '.showcase button', '#examples button',
    '[class*="case" i]', '[id*="case" i]', '[class*="example" i] button'];
  selectors.forEach(function (sel) {
    try {
      Array.prototype.forEach.call(document.querySelectorAll(sel), push);
    } catch (e) {}
  });
  try {
    Array.prototype.forEach.call(document.querySelectorAll('button, a, [role="button"], li'), function (el) {
      var label = textOf(el);
      var attrs = String((el.getAttribute('data-case-id') || '') + ' ' +
        (el.getAttribute('data-case') || '') + ' ' + (el.id || '') + ' ' +
        (el.className || ''));
      var hit = /案例|病例|case/i.test(attrs) || /案例/.test(label);
      titles.forEach(function (title) {
        if (title && label.indexOf(String(title)) >= 0) { hit = true; }
      });
      if (hit) { push(el); }
    });
  } catch (e) {}
  var bodyText = '';
  try { bodyText = textOf(document.body); } catch (e) {}
  var titleHits = 0;
  titles.forEach(function (title) {
    if (title && bodyText.indexOf(String(title)) >= 0) { titleHits += 1; }
  });
  // 案例标题所在的叶子节点也算作一个案例元素，兼容卡片/列表等不同渲染方式。
  try {
    Array.prototype.forEach.call(document.querySelectorAll('body *'), function (el) {
      if (el.children && el.children.length) { return; }
      var label = textOf(el);
      if (!label) { return; }
      titles.forEach(function (title) {
        var key = String(title || '');
        if (!key) { return; }
        var probeKey = key.length > 8 ? key.slice(0, 8) : key;
        if (label.indexOf(key) >= 0 || (probeKey.length >= 4 && label.indexOf(probeKey) >= 0)) {
          push(el);
        }
      });
    });
  } catch (e) {}
  var stylesheets = [];
  try {
    Array.prototype.forEach.call(document.querySelectorAll('link[rel="stylesheet"]'), function (el) {
      if (el.href && stylesheets.indexOf(el.href) < 0) { stylesheets.push(el.href); }
    });
  } catch (e) {}
  try {
    Array.prototype.forEach.call(document.styleSheets || [], function (sheet) {
      if (sheet && sheet.href && stylesheets.indexOf(sheet.href) < 0) { stylesheets.push(sheet.href); }
    });
  } catch (e) {}
  var cssHref = '';
  for (var i = 0; i < stylesheets.length; i += 1) {
    if (stylesheets[i].indexOf('presentation.css') >= 0) { cssHref = stylesheets[i]; break; }
  }
  if (!cssHref && stylesheets.length) { cssHref = stylesheets[0]; }
  var caseLabels = [];
  candidates.forEach(function (el) {
    var label = textOf(el).replace(/\s+/g, ' ').slice(0, 120);
    if (label && caseLabels.indexOf(label) < 0) { caseLabels.push(label); }
  });
  return {
    ready: document.readyState,
    caseButtons: document.querySelectorAll('#caseButtons button').length,
    caseTitles: titleHits,
    caseLabels: caseLabels.slice(0, 20),
    cssHref: cssHref || '',
    stylesheetHrefs: stylesheets,
    documentTitle: document.title || '',
    userAgent: navigator.userAgent || '',
    innerWidth: window.innerWidth || 0,
    innerHeight: window.innerHeight || 0,
    outerWidth: window.outerWidth || 0,
    outerHeight: window.outerHeight || 0,
    bodyTextLength: bodyText.length
  };
})();
"""


def _smoke_script(titles):
    return _SMOKE_JS.replace('__TITLES__', json.dumps(list(titles), ensure_ascii=False))


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _css_ok(href, base_url):
    if not href:
        return False
    return 'presentation.css' in href or str(href).startswith(base_url)


def _smoke_checks(data, base_url):
    ready = str(data.get('ready', '')).lower() in ('interactive', 'complete')
    count = _to_int(data.get('caseButtons'))
    css_href = str(data.get('cssHref') or '')
    return [
        {'name': 'page_ready', 'ok': ready, 'detail': {'readyState': str(data.get('ready', ''))}},
        {'name': 'case_buttons', 'ok': count >= 2, 'detail': {
            'count': count, 'title_matches': _to_int(data.get('caseTitles')),
        }},
        {'name': 'static_css', 'ok': _css_ok(css_href, base_url), 'detail': {
            'href': css_href, 'stylesheets': data.get('stylesheetHrefs') or [],
        }},
    ]


def _probe_passes(data, base_url):
    return all(check['ok'] for check in _smoke_checks(data, base_url))


def _safe_destroy(window):
    if window is None:
        return
    try:
        window.destroy()
    except Exception:
        pass


def _configure_webview(webview):
    settings = getattr(webview, 'settings', None)
    if settings is None:
        return
    try:
        settings['ALLOW_DOWNLOADS'] = True
    except Exception:
        pass
    try:
        settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    except Exception:
        pass


def run_smoke_test(report_path=None, timeout=SMOKE_TIMEOUT):
    timeout = float(timeout)
    started = time.monotonic()
    started_at = _timestamp()
    deadline = started + timeout
    backend = BackendProcess()
    checks = []
    error = None
    data = None
    window = None
    expected_titles = []
    stop_watchdog = threading.Event()
    state = {'done': False, 'data': None, 'error': None}

    def watchdog():
        while not stop_watchdog.wait(0.2):
            if time.monotonic() < deadline:
                continue
            if not state['done']:
                state['error'] = state['error'] or ('冒烟测试超时（最多 %.0f 秒）' % timeout)
                _safe_destroy(window)
            return

    try:
        try:
            start_budget = min(
                BACKEND_START_TIMEOUT, max(0.1, deadline - time.monotonic())
            )
            backend.start(timeout=start_budget)
            checks.append({'name': 'backend_start', 'ok': True, 'detail': {
                'base_url': backend.base_url, 'log': str(backend.log_path),
            }})
        except Exception as exc:
            error = '本地服务启动失败：%s' % exc
            checks.append({'name': 'backend_start', 'ok': False, 'detail': str(exc)})
            raise

        try:
            import webview
        except Exception as exc:
            raise RuntimeError('缺少桌面窗口组件 pywebview：%s' % exc) from exc
        _configure_webview(webview)

        try:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                status, _, raw = _request_bytes(
                    backend.base_url + '/api/cases', timeout=min(3.0, max(0.2, remaining))
                )
                if status == 200:
                    expected_titles = _case_titles(_case_list(_decode_json(raw)))
        except Exception:
            expected_titles = []

        window = webview.create_window(
            _app_title(),
            backend.base_url,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=WINDOW_MIN_SIZE,
            hidden=True,
            background_color=WINDOW_BACKGROUND,
            text_select=True,
        )
        checks.append({'name': 'window_created', 'ok': True, 'detail': {
            'hidden': True, 'width': WINDOW_WIDTH, 'height': WINDOW_HEIGHT,
        }})

        loaded = threading.Event()
        try:
            window.events.loaded += lambda: loaded.set()
        except Exception:
            pass

        def worker():
            try:
                remaining = max(0.0, deadline - time.monotonic())
                loaded.wait(min(5.0, remaining))
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        state['error'] = state['error'] or '页面在限定时间内未完成渲染'
                        break
                    try:
                        probe = window.evaluate_js(_smoke_script(expected_titles))
                        if isinstance(probe, str):
                            try:
                                probe = json.loads(probe)
                            except ValueError:
                                probe = None
                    except Exception as exc:
                        # 页面尚未完成初始化时 evaluate_js 可能失败，继续轮询。
                        state['error'] = '无法读取页面状态：%s' % exc
                        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                        continue
                    if isinstance(probe, dict):
                        state['data'] = probe
                        if _probe_passes(probe, backend.base_url):
                            state['error'] = None
                            break
                    time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
            except Exception as exc:
                state['error'] = '%s: %s' % (type(exc).__name__, exc)
            finally:
                state['done'] = True
                _safe_destroy(window)
                stop_watchdog.set()

        threading.Thread(target=watchdog, name='smoke-watchdog', daemon=True).start()
        webview.start(
            func=worker,
            gui='edgechromium',
            private_mode=False,
            storage_path=_browser_storage_path(backend.data_dir),
        )
        data = state['data']
        if data is None:
            error = state['error'] or '未获取到页面状态'
            checks.append({'name': 'page_probe', 'ok': False, 'detail': error})
        else:
            checks.extend(_smoke_checks(data, backend.base_url))
            if not all(check['ok'] for check in checks):
                error = state['error'] or '冒烟检查未通过'
            elif state['error']:
                error = state['error']
    except Exception as exc:
        error = error or ('冒烟测试失败：%s' % exc)
        if not any(check['name'] == 'page_probe' for check in checks):
            checks.append({'name': 'page_probe', 'ok': False, 'detail': str(exc)})
        _friendly_failure('冒烟测试失败', exc, backend.data_dir, show_messagebox=False)
    finally:
        stop_watchdog.set()
        _safe_destroy(window)
        try:
            backend.close()
        except Exception as exc:
            if error is None:
                error = '本地服务停止失败：%s' % exc
        checks.append({
            'name': 'backend_stopped',
            'ok': backend.process is None,
            'detail': {'owned_child_released': backend.process is None},
        })

    if data is None:
        data = state.get('data') or {}
    all_ok = error is None and data is not None and bool(checks) and all(check['ok'] for check in checks)
    rendered_cases = [str(label) for label in (data.get('caseLabels') or [])]
    report = {
        'ok': all_ok,
        'kind': 'smoke-test',
        'app_id': APP_ID,
        'app_name': APP_NAME,
        'version': VERSION,
        'started_at': started_at,
        'duration_seconds': round(time.monotonic() - started, 3),
        'title': _app_title(),
        'window_title': _app_title(),
        'document_title': data.get('documentTitle', ''),
        'url': backend.base_url,
        'user_agent': data.get('userAgent', ''),
        'window_width': _to_int(data.get('outerWidth')) or _to_int(data.get('innerWidth')),
        'window_height': _to_int(data.get('outerHeight')) or _to_int(data.get('innerHeight')),
        'viewport_width': _to_int(data.get('innerWidth')),
        'viewport_height': _to_int(data.get('innerHeight')),
        'available_cases': expected_titles or rendered_cases,
        'rendered_cases': rendered_cases,
        'case_buttons': _to_int(data.get('caseButtons')),
        'css_href': data.get('cssHref', ''),
        'checks': checks,
        'backend_log': str(backend.log_path) if backend.log_path else None,
        'error': error,
    }
    try:
        _write_report(report_path, report, 'smoke-test-report.json')
    except Exception as exc:
        _friendly_failure('无法写入冒烟测试报告', exc, backend.data_dir, show_messagebox=False)
        return 1
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# 普通桌面窗口
# ---------------------------------------------------------------------------

def _webview_failure_text(exc):
    return (
        '无法打开本机原生窗口：%s\n\n'
        '请确认系统已安装 Microsoft Edge WebView2 运行时，然后重新启动程序。\n'
        '程序不会自动下载或安装组件，也不会回退到旧版 Internet Explorer。'
        % exc
    )


def run_app():
    data_dir = _data_dir()
    backend = BackendProcess()
    try:
        backend.start()
    except Exception as exc:
        backend.close()
        _friendly_failure('本地服务启动失败', str(exc), data_dir, show_messagebox=True)
        return 1

    window = None
    failure = None
    try:
        try:
            import webview
        except Exception as exc:
            raise RuntimeError('缺少桌面窗口组件 pywebview：%s' % exc) from exc
        _configure_webview(webview)
        window = webview.create_window(
            _app_title(),
            backend.base_url,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=WINDOW_MIN_SIZE,
            background_color=WINDOW_BACKGROUND,
            text_select=True,
        )
        # edgechromium 即 WebView2；不提供 IE 回退。private_mode=False 配合
        # storage_path 让 localStorage 持久保存到用户目录。
        webview.start(
            gui='edgechromium',
            private_mode=False,
            storage_path=_browser_storage_path(data_dir),
        )
        return 0
    except Exception as exc:
        failure = exc
        _safe_destroy(window)
        return 1
    finally:
        # 先停止本机后端，再弹出阻塞式错误提示。
        backend.close()
        if failure is not None:
            _friendly_failure('桌面窗口启动失败', _webview_failure_text(failure),
                              data_dir, show_messagebox=True)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        prog='desktop_app.py',
        description='%s %s 本机桌面程序' % (APP_NAME, VERSION),
    )
    parser.add_argument('--serve', action='store_true',
                        help='内部使用：以本机 HTTP 后端子进程运行，不打开窗口')
    parser.add_argument('--port', type=int, default=None,
                        help='内部使用：--serve 绑定的 127.0.0.1 端口')
    parser.add_argument('--self-test', action='store_true',
                        help='运行无 GUI 的真实后端自检并写出报告')
    parser.add_argument('--smoke-test', action='store_true',
                        help='启动隐藏的原生 WebView2 窗口完成冒烟测试并写出报告')
    parser.add_argument('--report', default=None,
                        help='--self-test / --smoke-test 的 JSON 报告路径')
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.serve:
        try:
            return _run_backend(args.port if args.port is not None else DEFAULT_BACKEND_PORT)
        except Exception as exc:
            _friendly_failure('内部后端启动失败', exc, show_messagebox=False)
            return 1
    if args.self_test:
        return run_self_test(args.report)
    if args.smoke_test:
        return run_smoke_test(args.report)
    return run_app()


if __name__ == '__main__':
    sys.exit(main())
