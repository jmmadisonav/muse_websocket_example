"""Minimal HTTP + WebSocket (RFC 6455) server built on the Python standard library.

One TCP port serves both:
  - Plain HTTP GET requests for static files in the web root (for example, index.html).
  - WebSocket upgrade requests on any path.

The server needs no third-party packages, so you can run it on a MUSE controller
without a requirements.txt file or internet access.
"""

import base64
import hashlib
import mimetypes
import os
import socketserver
import struct
import threading
from typing import Callable, Optional

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_MESSAGE_SIZE = 1024 * 1024  # Rejects anything over 1 MB.


class WebSocketClient:
    """A single connected browser. Use send() to push text to it."""

    def __init__(self, sock, address):
        self.sock = sock
        self.address = "%s:%s" % address
        self._send_lock = threading.Lock()
        self.open = True

    def send(self, text):
        """Sends a text message. Returns False if the client has gone away."""
        return self._send_frame(OP_TEXT, text.encode("utf-8"))

    def close(self, code=1000):
        self._send_frame(OP_CLOSE, struct.pack("!H", code))
        self.open = False

    def _send_frame(self, opcode, payload):
        if not self.open:
            return False
        header = bytearray([0x80 | opcode])  # FIN bit set, no fragmentation.
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        try:
            with self._send_lock:
                self.sock.sendall(bytes(header) + payload)
            return True
        except OSError:
            self.open = False
            return False


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server.app
        self.request.settimeout(None)
        reader = self.request.makefile("rb")

        request_line, headers = self._read_http_head(reader)
        if request_line is None:
            return
        parts = request_line.split(" ")
        if len(parts) < 2:
            return
        method, path = parts[0], parts[1].split("?", 1)[0]

        if headers.get("upgrade", "").lower() == "websocket":
            self._handle_websocket(server, reader, headers)
        elif method == "GET":
            self._serve_file(server, path)
        else:
            self._send_http(405, "text/plain", b"Method Not Allowed")

    # ---- HTTP -------------------------------------------------------------

    @staticmethod
    def _read_http_head(reader):
        try:
            request_line = reader.readline(8192).decode("latin-1").strip()
            headers = {}
            while True:
                line = reader.readline(8192).decode("latin-1")
                if line in ("\r\n", "\n", ""):
                    break
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            return request_line, headers
        except OSError:
            return None, None

    def _send_http(self, status, content_type, body, extra_headers=None):
        reasons = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}
        head = "HTTP/1.1 %d %s\r\n" % (status, reasons.get(status, ""))
        head += "Content-Type: %s\r\n" % content_type
        head += "Content-Length: %d\r\n" % len(body)
        head += "Cache-Control: no-cache\r\n"
        head += "Connection: close\r\n"
        for key, value in (extra_headers or {}).items():
            head += "%s: %s\r\n" % (key, value)
        self.request.sendall(head.encode("latin-1") + b"\r\n" + body)

    def _serve_file(self, server, path):
        if path == "/":
            path = "/index.html"
        # Resolve inside the web root only, so "/../main.py" can't escape it.
        full_path = os.path.realpath(os.path.join(server.web_root, path.lstrip("/")))
        if not full_path.startswith(server.web_root + os.sep) or not os.path.isfile(full_path):
            self._send_http(404, "text/plain", b"Not Found")
            return
        content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        with open(full_path, "rb") as f:
            self._send_http(200, content_type, f.read())

    # ---- WebSocket ---------------------------------------------------------

    def _handle_websocket(self, server, reader, headers):
        key = headers.get("sec-websocket-key")
        if not key:
            self._send_http(400, "text/plain", b"Missing Sec-WebSocket-Key")
            return
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        self.request.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % accept
            ).encode("latin-1")
        )

        client = WebSocketClient(self.request, self.client_address)
        server._add_client(client)
        try:
            self._websocket_loop(server, reader, client)
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            client.open = False
            server._remove_client(client)

    def _websocket_loop(self, server, reader, client):
        fragments = bytearray()
        fragment_opcode = None

        while client.open:
            head = self._read_exact(reader, 2)
            if head is None:
                return
            fin = head[0] & 0x80
            opcode = head[0] & 0x0F
            masked = head[1] & 0x80
            length = head[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(reader, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(reader, 8))[0]
            if length > MAX_MESSAGE_SIZE:
                client.close(1009)
                return

            mask = self._read_exact(reader, 4) if masked else b"\x00\x00\x00\x00"
            payload = bytearray(self._read_exact(reader, length) or b"")
            for i in range(length):
                payload[i] ^= mask[i % 4]

            if opcode == OP_CLOSE:
                client.close()
                return
            if opcode == OP_PING:
                client._send_frame(OP_PONG, bytes(payload))
                continue
            if opcode == OP_PONG:
                continue

            # Reassemble fragmented messages before handing them to the app.
            if opcode in (OP_TEXT, OP_BINARY):
                fragment_opcode = opcode
                fragments = payload
            elif opcode == OP_CONTINUATION:
                fragments += payload
            if not fin:
                continue

            if fragment_opcode == OP_TEXT:
                server._dispatch_message(client, fragments.decode("utf-8", errors="replace"))
            fragments = bytearray()
            fragment_opcode = None

    @staticmethod
    def _read_exact(reader, count):
        data = reader.read(count)
        if data is None or len(data) < count:
            return None
        return data


class _ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class WebSocketServer:
    """Serves files from web_root over HTTP and accepts WebSocket clients on the same port.

    Assign these optional callbacks before you call start():
      on_connect(client)
      on_message(client, text)
      on_disconnect(client)

    Callbacks run on the client's own connection thread.
    """

    def __init__(self, port=8080, web_root=".", host="0.0.0.0"):
        self.host = host
        self.port = port
        self.web_root = os.path.realpath(web_root)
        self.on_connect: Optional[Callable[[WebSocketClient], None]] = None
        self.on_message: Optional[Callable[[WebSocketClient, str], None]] = None
        self.on_disconnect: Optional[Callable[[WebSocketClient], None]] = None
        self._clients = set()
        self._clients_lock = threading.Lock()
        self._server = None
        self._thread = None

    @property
    def clients(self):
        with self._clients_lock:
            return list(self._clients)

    def start(self):
        self._server = _ThreadingServer((self.host, self.port), _Handler)
        self._server.app = self
        self._thread = threading.Thread(target=self._server.serve_forever, name="websocket-server", daemon=True)
        self._thread.start()

    def stop(self):
        for client in self.clients:
            client.close(1001)
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def broadcast(self, text):
        """Sends text to every connected client."""
        for client in self.clients:
            client.send(text)

    # ---- Internal ----------------------------------------------------------

    def _add_client(self, client):
        with self._clients_lock:
            self._clients.add(client)
        self._call(self.on_connect, client)

    def _remove_client(self, client):
        with self._clients_lock:
            if client not in self._clients:
                return
            self._clients.discard(client)
        self._call(self.on_disconnect, client)

    def _dispatch_message(self, client, text):
        self._call(self.on_message, client, text)

    @staticmethod
    def _call(callback, *args):
        if callback:
            callback(*args)
