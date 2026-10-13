"""Native local HTTP/process controls. DO NOT execute without a named heavy lease."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest

from crm_agent.connectors.salesforce import SalesforceError, SalesforceSnapshot, query_url
from crm_agent.connectors.salesforce_http import SalesforceHttp
from crm_agent.protocol import encode
from tests.test_salesforce_contract import config, packets


@contextmanager
def server(responses, *, block=False, drop=False, status=200, raw=None):
    calls, handlers = [], []
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            handlers.append(threading.current_thread())
            calls.append({"method":"GET","path":self.path,"authorization_present":self.headers.get("Authorization") is not None})
            entered.set()
            if block:
                release.wait(2)
            if drop:
                self.close_connection = True
                return
            body = raw if raw is not None else encode(responses.pop(0))
            try:
                self.send_response(status)
                self.send_header("Content-Type","application/json")
                self.send_header("Sforce-Limit-Info","api-usage=1/5000")
                if status == 302:
                    self.send_header("Location","https://attacker.invalid/never-follow")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                pass
    http = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=lambda:http.serve_forever(poll_interval=0.01))
    thread.start()
    try:
        yield "http://127.0.0.1:"+str(http.server_port),calls,entered,release
    finally:
        release.set()
        http.shutdown()
        thread.join(1)
        http.server_close()
        for handler in handlers:
            handler.join(1)
        assert not thread.is_alive() and all(not h.is_alive() for h in handlers)


class SalesforceNativeHttpTests(unittest.TestCase):
    def test_read_only_snapshot_uses_real_owned_http_children_without_credentials(self):
        with server(packets()) as (base,calls,_,__):
            transport = SalesforceHttp(config(),local_base_url=base)
            result = SalesforceSnapshot(config(),transport).snapshot()
            self.assertEqual(result["mode"],"local-http-double")
            self.assertEqual(len(calls),6)
            self.assertTrue(all(c["method"]=="GET" and not c["authorization_present"] for c in calls))
            self.assertEqual(len(transport.records),6)
            self.assertTrue(all(p["owned_child_exited"] and p["exit_code"]==0 and p["retries"]==0 for p in transport.records))

    def test_delayed_headers_end_inside_caller_budget_with_owned_cleanup(self):
        with server(packets(),block=True) as (base,calls,_,release):
            transport = SalesforceHttp(config(),local_base_url=base)
            start = time.monotonic()
            with self.assertRaises(SalesforceError) as caught:
                transport.get(query_url(config(),"Opportunity"),deadline=start+0.8,max_bytes=1000)
            self.assertEqual(caught.exception.code,"DEADLINE")
            self.assertLess(time.monotonic()-start,1.2)
            self.assertEqual(len(calls),1)
            self.assertTrue(transport.records[-1]["owned_child_exited"])
            release.set()

    def test_inflight_cancel_kills_only_owned_http_child(self):
        with server(packets(),block=True) as (base,calls,entered,release):
            transport = SalesforceHttp(config(),local_base_url=base)
            cancel = threading.Event()
            errors = []
            def request():
                try:
                    transport.get(query_url(config(),"Opportunity"),deadline=time.monotonic()+2,
                                  max_bytes=1000,cancelled=cancel.is_set)
                except SalesforceError as e:
                    errors.append(e.code)
            caller = threading.Thread(target=request)
            caller.start()
            self.assertTrue(entered.wait(1))
            cancel.set()
            caller.join(1)
            release.set()
            self.assertFalse(caller.is_alive())
            self.assertEqual(errors,["CANCELLED"])
            self.assertEqual(len(calls),1)
            self.assertTrue(transport.records[-1]["owned_child_exited"])

    def test_redirect_drop_size_and_status_have_no_retry_or_redirect(self):
        for status in [302,401,403,429,503]:
            with self.subTest(status=status), server([{"error":"synthetic"}],status=status) as (base,calls,_,__):
                transport = SalesforceHttp(config(),local_base_url=base)
                result = transport.get(query_url(config(),"Opportunity"),deadline=time.monotonic()+2,max_bytes=1000)
                self.assertEqual(result.status,status)
                self.assertEqual(len(calls),1)
                self.assertTrue(transport.records[-1]["owned_child_exited"])
        for options,expected in [({"drop":True},"TRANSPORT_ERROR"),({"raw":b"x"*1001},"BODY_LIMIT")]:
            with self.subTest(options=options), server([{}],**options) as (base,calls,_,__):
                transport = SalesforceHttp(config(),local_base_url=base)
                with self.assertRaises(SalesforceError) as caught:
                    transport.get(query_url(config(),"Opportunity"),deadline=time.monotonic()+2,max_bytes=1000)
                self.assertEqual(caught.exception.code,expected)
                self.assertEqual(len(calls),1)
                self.assertTrue(transport.records[-1]["owned_child_exited"])

