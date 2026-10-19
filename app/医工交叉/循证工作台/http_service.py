"""Local research workbench HTTP service (not a public production server)."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import demo_data
import orchestrator
import workbench_jobs

BASE = Path(orchestrator.HERE)
MAX_BODY = 2 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = 'EvidenceWorkbench/1.0.1'

    def _local_request(self):
        """Reject other-site browser calls and unexpected Host/DNS names."""
        host = self.headers.get('Host', '')
        expected = {'127.0.0.1:%s' % self.server.server_address[1], 'localhost:%s' % self.server.server_address[1]}
        origin = self.headers.get('Origin')
        valid = host in expected and (not origin or origin in {'http://' + host})
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            valid = False
        if not valid:
            self._send(403, {'ok': False, 'error': '仅接受本机工作台页面发起的请求'})
        return valid

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):
        if not self._local_request():
            return
        url = urlsplit(self.path)
        try:
            if url.path in ('/', '/index.html'):
                self._send(200, (BASE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif url.path == '/presentation.css':
                self._send(200, (BASE / 'presentation.css').read_bytes(), 'text/css; charset=utf-8')
            elif url.path == '/api/health':  # research diagnostics only
                self._send(200, orchestrator.health())
            elif url.path == '/api/cases':
                self._send(200, orchestrator.showcase_cases())
            elif url.path == '/api/jobs':
                self._send(200, workbench_jobs.get(parse_qs(url.query).get('id', [''])[0]))
            elif url.path == '/api/export':
                identity = parse_qs(url.query).get('id', [''])[0]
                self._send(200, orchestrator.export_bundle(identity), 'application/zip')
            elif url.path == '/api/example':
                self._send(200, dict(ok=True, pico=demo_data.DEFAULT_PICO, task_yaml=demo_data.live_task_yaml(demo_data.TASK_YAML, ['pubmed', 'openalex']), refs=[]))
            elif url.path == '/api/examples':
                self._send(200, {'ok': True, 'examples': demo_data.live_examples()})
            elif url.path == '/api/runs':
                self._send(200, orchestrator.list_runs())
            elif url.path == '/api/run':
                self._send(200, orchestrator.load_run(parse_qs(url.query).get('id', [''])[0]))
            elif url.path in ('/health', '/healthz'):
                self._send(200, 'OK', 'text/plain; charset=utf-8')
            else:
                self._send(404, {'ok': False, 'error': '未找到该地址'})
        except FileNotFoundError:
            self._send(404, {'ok': False, 'error': '运行记录不存在'})
        except (ValueError, KeyError) as exc:
            self._send(400, {'ok': False, 'error': str(exc)})
        except Exception:
            self._send(500, {'ok': False, 'error': '读取失败，请检查服务端日志'})

    def do_POST(self):
        if not self._local_request():
            return
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                self._send(413, {'ok': False, 'error': '请求体为空或过大'})
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('请求必须是 JSON 对象')
            if path == '/api/run':
                result = orchestrator.run_all(pico=payload.get('pico', ''), online=payload.get('online') is True,
                                               task_yaml=payload.get('task_yaml', ''), query=payload.get('query', ''))
            elif path == '/api/jobs/review':
                result = workbench_jobs.submit('review', lambda progress: orchestrator.review_step(
                    payload.get('task_yaml', ''), query=payload.get('query', ''), pico=payload.get('pico', ''), progress=progress))
            elif path == '/api/task':
                result = orchestrator.task_card(payload.get('task_yaml', ''), payload.get('updates'))
            elif path == '/api/cases/replay':
                result = orchestrator.replay_case(payload.get('case_id'))
            elif path == '/api/review':
                result = orchestrator.review_step(payload.get('task_yaml', ''), query=payload.get('query', ''), pico=payload.get('pico', ''))
            elif path == '/api/verify':
                result = orchestrator.verify_step(payload.get('refs', []), online=payload.get('online') is True, run_id=payload.get('run_id'))
            elif path == '/api/screen':
                result = orchestrator.review_decisions(payload.get('run_id'), payload.get('decisions'))
            elif path == '/api/runs/archive':
                result = orchestrator.archive_run(payload.get('run_id'))
            elif path == '/api/mesh':
                result = orchestrator.mesh_step(payload.get('pico', ''))
            elif path == '/api/draft':
                result = orchestrator.draft_step(payload.get('pico', ''))
            elif path == '/api/count':
                result = orchestrator.count_step(payload.get('query', ''), payload.get('task_yaml', ''))
            elif path == '/api/tune':
                result = orchestrator.tune_step(payload.get('items', []))
            elif path == '/api/sq3r':
                result = orchestrator.sq3r_step(payload.get('run_id'))
            else:
                self._send(404, {'ok': False, 'error': '未找到该接口'})
                return
            self._send(200, result)
        except FileNotFoundError:
            self._send(404, {'ok': False, 'error': '运行记录不存在'})
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self._send(400, {'ok': False, 'error': '请求无效：%s' % exc})
        except Exception as exc:
            print('request failed:', type(exc).__name__, file=sys.stderr)
            self._send(500, {'ok': False, 'error': '请求失败，未生成替代结果'})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8130
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('循证科研工作台：http://127.0.0.1:%d/' % port)
    print('本地单用户试用；演示与真实来源隔离，未联网不显示核验通过。')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
