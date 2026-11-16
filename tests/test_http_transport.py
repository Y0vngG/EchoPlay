import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request
from unittest.mock import patch

from echoplay.services.http_transport import PersistentRangeClient


class PersistentTransportTests(unittest.TestCase):
    def test_ranges_reuse_connection(self):
        ports = []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                ports.append(self.client_address[1])
                self.send_response(206)
                self.send_header("Content-Length", "3")
                self.send_header("Content-Range", "bytes 0-2/3")
                self.end_headers()
                self.wfile.write(b"abc")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = PersistentRangeClient()
        try:
            with patch("echoplay.services.http_transport.proxy_bypass", return_value=True):
                for _ in range(2):
                    request = Request(f"http://127.0.0.1:{server.server_port}/audio",
                                      headers={"Range": "bytes=0-2"})
                    with client.open(request, timeout=2) as response:
                        self.assertEqual(response.read(), b"abc")
            self.assertEqual(len(ports), 2)
            self.assertEqual(ports[0], ports[1])
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(2)
