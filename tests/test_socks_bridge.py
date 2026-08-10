from __future__ import annotations

import socket
import socketserver
import threading
import unittest

from socks_bridge import AuthenticatedSocksBridge, RemoteSocks


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError
        data += chunk
    return data


class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request.recv(1024)
        self.request.sendall(data)


class FakeRemoteSocksHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock = self.request
        version, count = recv_exact(sock, 2)
        self.assert_equal(version, 5)
        recv_exact(sock, count)
        sock.sendall(b"\x05\x02")
        self.assert_equal(recv_exact(sock, 1), b"\x01")
        username = recv_exact(sock, recv_exact(sock, 1)[0])
        password = recv_exact(sock, recv_exact(sock, 1)[0])
        self.assert_equal((username, password), (b"user", b"pass###"))
        sock.sendall(b"\x01\x00")
        header = recv_exact(sock, 4)
        self.assert_equal(header[:3], b"\x05\x01\x00")
        if header[3] == 1:
            host = socket.inet_ntoa(recv_exact(sock, 4))
        else:
            host = recv_exact(sock, recv_exact(sock, 1)[0]).decode()
        port = int.from_bytes(recv_exact(sock, 2), "big")
        upstream = socket.create_connection((host, port), timeout=5)
        try:
            sock.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
            payload = sock.recv(1024)
            upstream.sendall(payload)
            sock.sendall(upstream.recv(1024))
        finally:
            upstream.close()

    @staticmethod
    def assert_equal(left: object, right: object) -> None:
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class SocksBridgeTests(unittest.TestCase):
    def test_authenticated_remote_and_local_no_auth_relay(self) -> None:
        echo = ThreadedServer(("127.0.0.1", 0), EchoHandler)
        remote = ThreadedServer(("127.0.0.1", 0), FakeRemoteSocksHandler)
        echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
        remote_thread = threading.Thread(target=remote.serve_forever, daemon=True)
        echo_thread.start()
        remote_thread.start()
        bridge = AuthenticatedSocksBridge(
            RemoteSocks("127.0.0.1", remote.server_address[1], "user", "pass###")
        )
        bridge.start()
        try:
            client = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
            with client:
                client.sendall(b"\x05\x01\x00")
                self.assertEqual(recv_exact(client, 2), b"\x05\x00")
                target_port = int(echo.server_address[1])
                client.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + target_port.to_bytes(2, "big"))
                self.assertEqual(recv_exact(client, 10)[:2], b"\x05\x00")
                client.sendall(b"bridge-ok")
                self.assertEqual(recv_exact(client, 9), b"bridge-ok")
        finally:
            bridge.stop()
            remote.shutdown()
            echo.shutdown()
            remote.server_close()
            echo.server_close()


if __name__ == "__main__":
    unittest.main()
