"""Contrato HTTP e trabalhos assíncronos; somente endereços de loopback."""

from http.client import HTTPConnection
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from presets import make_preset
from server import Handler


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        data = json.dumps(body) if body is not None else None
        connection.request(method, path, body=data, headers={'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def test_metadata_and_analysis(self):
        status, meta = self.request('GET', '/api/meta')
        self.assertEqual(status, 200)
        self.assertIn('mathematical', meta['references'])
        status, report = self.request('POST', '/api/analyze', make_preset('main'))
        self.assertEqual(status, 200)
        self.assertTrue(report['passed'])

    def test_validation_job_finishes_and_reports_cases(self):
        status, created = self.request('POST', '/api/jobs/validate', {'graph': make_preset('main'), 'options': {'randomCount': 0}})
        self.assertEqual(status, 202)
        limit = time.monotonic() + 8
        while time.monotonic() < limit:
            status, job = self.request('GET', '/api/jobs/' + created['jobId'])
            if job['status'] in ('done', 'error'):
                break
            time.sleep(.02)
        self.assertEqual(job['status'], 'done', job)
        self.assertEqual(job['result']['total'], 9)
        self.assertEqual(job['result']['passed'], 9)

    def test_cross_origin_and_bad_payloads_are_rejected(self):
        status, _ = self.request('POST', '/api/analyze', make_preset('main'), {'Origin': 'https://example.invalid'})
        self.assertEqual(status, 403)
        for body in ([], {'graph': None}, {'graph': make_preset('main'), 'options': {'samples': 0}}):
            status, response = self.request('POST', '/api/jobs/native', body)
            self.assertEqual(status, 400)
            self.assertIn('error', response)


if __name__ == '__main__':
    unittest.main(verbosity=2)
