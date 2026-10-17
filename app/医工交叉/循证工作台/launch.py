"""Double-click launcher: reuse this workbench or start its local service."""
import json
import sys
import threading
import urllib.request
import webbrowser

import orchestrator
import http_service


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8137
    url = 'http://127.0.0.1:%s/' % port
    try:
        with urllib.request.urlopen(url + 'api/health', timeout=1) as response:
            current = json.load(response)
        if current.get('root') == orchestrator.ROOT:
            webbrowser.open(url)
            return
        raise RuntimeError('端口已被其他服务占用，请选择另一个端口')
    except OSError:
        pass
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    sys.argv = ['serve.py', str(port)]
    http_service.main()


if __name__ == '__main__':
    main()
