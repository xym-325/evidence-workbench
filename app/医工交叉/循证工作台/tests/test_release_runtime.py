import os
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_paths as paths
import http_service


class RuntimePathsTests(unittest.TestCase):
    def test_frozen_resources_are_separate_from_user_outputs(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', 'C:/bundle', create=True), patch.dict(os.environ, {'EVIDENCE_WORKBENCH_DATA_DIR': str(Path.cwd() / 'test-profile')}):
            self.assertIn('workspace', str(paths.workbench_root()))
            self.assertEqual(paths.outputs_dir(), (Path.cwd() / 'test-profile' / 'outputs').resolve())
            self.assertNotIn('test-profile', str(paths.project_root()))

    def test_ordinary_web_development_retains_existing_outputs(self):
        with patch.object(sys, 'frozen', False, create=True), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.outputs_dir(), paths.workbench_root() / 'outputs')


class LocalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.server = http_service.ThreadingHTTPServer(('127.0.0.1', 0), http_service.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:%s/healthz' % self.server.server_address[1]

    def test_external_origin_is_rejected_before_any_operation(self):
        req = urllib.request.Request(self.url, headers={'Origin': 'https://other-site.example'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 403)

    def test_unknown_host_is_rejected(self):
        req = urllib.request.Request(self.url, headers={'Host': 'other-site.example'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 403)

    def test_local_health_still_works(self):
        with urllib.request.urlopen(self.url) as response:
            self.assertEqual(response.read(), b'OK')

    def test_removed_model_endpoints_do_not_start_a_job(self):
        base = self.url.removesuffix('/healthz')
        with patch.object(http_service.workbench_jobs, 'submit', side_effect=AssertionError('Removed feature must not start jobs')):
            requests = [urllib.request.Request(base + '/api/capabilities'),
                        urllib.request.Request(base + '/api/jobs/synthesis',
                                               data=json.dumps({'run_id': 'a' * 32, 'provider': 'claude-cli'}).encode(),
                                               headers={'Content-Type': 'application/json'})]
            for request in requests:
                with self.subTest(url=request.full_url):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
