# -*- coding: utf-8 -*-
"""学术智能体 · 本地服务器

用法: python serve.py [端口]     (默认 8000)
浏览器打开 http://localhost:8000/

相比 python -m http.server 多做三件事：
  1. 响应头显式带 charset=utf-8，避免中文乱码；
  2. 禁用缓存，保证验收看到的是最新页面；
  3. 提供 POST /api/run，把任务卡交给 agent 跑「检索→筛选→综述」全流程。
"""
import io
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent

BASE = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 2 * 1024 * 1024          # 请求体上限 2MB


class Handler(BaseHTTPRequestHandler):
    server_version = "AcademicAgent/0.1"

    # -------------------------------------------------- 基础发送
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def _send_json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _read_file(self, name):
        with io.open(os.path.join(BASE, name), "rb") as handle:
            return handle.read()

    # -------------------------------------------------- GET
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send(200, self._read_file("index.html"), "text/html; charset=utf-8")
            elif path == "/task.example.yaml":
                self._send(200, self._read_file("task.example.yaml"),
                           "text/yaml; charset=utf-8")
            elif path == "/api/example":
                text = self._read_file("task.example.yaml").decode("utf-8", "replace")
                self._send_json({"ok": True, "yaml": text})
            elif path in ("/health", "/healthz"):
                self._send(200, "OK")
            else:
                self._send(404, "没有这个地址：%s（可用：/ 、/api/run 、/api/example）" % path)
        except Exception as exc:  # noqa: BLE001 页面永不白屏
            traceback.print_exc()
            self._send(500, "服务内部错误：%s" % exc)

    # -------------------------------------------------- POST
    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/api/run":
            self._send(404, "没有这个接口：%s" % path)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._send_json({"ok": False, "errors": ["请求内容为空，请先填写任务卡"]})
                return
            if length > MAX_BODY:
                self._send_json({"ok": False,
                                 "errors": ["任务卡过大（上限 2MB），请精简后再试"]})
                return
            raw = self.rfile.read(length).decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
                text = (payload or {}).get("yaml") or ""
            except Exception:  # noqa: BLE001
                self._send_json({"ok": False,
                                 "errors": ["请求格式错误：应为带 yaml 字段的 JSON"]})
                return
            result = agent.run_task(text)
            self._send_json(result)
        except Exception as exc:  # noqa: BLE001 所有异常转友好提示
            traceback.print_exc()
            self._send_json({"ok": False,
                             "errors": ["服务内部错误：%s（已捕获，服务可继续使用）" % exc]})

    def log_message(self, fmt, *args):
        sys.stderr.write("[serve] %s\n" % (fmt % args))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("学术智能体: http://localhost:%d/   (0.0.0.0:%d 已对外)" % (port, port))
    print("无网环境可用 mock 源跑通全流程；结束请按 Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
