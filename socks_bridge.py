from __future__ import annotations

import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("SOCKS connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_address(sock: socket.socket, atyp: int) -> bytes:
    if atyp == 1:
        return _recv_exact(sock, 4)
    if atyp == 3:
        length = _recv_exact(sock, 1)
        return length + _recv_exact(sock, length[0])
    if atyp == 4:
        return _recv_exact(sock, 16)
    raise ValueError(f"unsupported SOCKS address type: {atyp}")


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], remote: "RemoteSocks") -> None:
        self.remote = remote
        self.last_error = ""
        super().__init__(address, _BridgeHandler)


class _BridgeHandler(socketserver.BaseRequestHandler):
    server: _BridgeServer

    def _remote_connect_once(self, request_packet: bytes) -> tuple[socket.socket, bytes]:
        spec = self.server.remote
        remote = socket.create_connection((spec.host, spec.port), timeout=20)
        try:
            remote.settimeout(20)
            remote.sendall(b"\x05\x02\x00\x02")
            version, method = _recv_exact(remote, 2)
            if version != 5 or method == 0xFF:
                raise ConnectionError("remote SOCKS authentication method rejected")
            if method == 2:
                username = spec.username.encode("utf-8")
                password = spec.password.encode("utf-8")
                if len(username) > 255 or len(password) > 255:
                    raise ValueError("SOCKS username/password exceeds 255 bytes")
                remote.sendall(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
                auth_version, status = _recv_exact(remote, 2)
                if auth_version != 1 or status != 0:
                    raise ConnectionError("remote SOCKS authentication failed")
            remote.sendall(request_packet)
            header = _recv_exact(remote, 4)
            address = _read_address(remote, header[3])
            port = _recv_exact(remote, 2)
            response = header + address + port
            if header[1] != 0:
                raise ConnectionError(f"remote SOCKS connect failed: {header[1]}")
            return remote, response
        except BaseException:
            remote.close()
            raise

    def _remote_connect(self, request_packet: bytes) -> tuple[socket.socket, bytes]:
        """在把失败返回 Chromium 前短暂重试，吸收动态代理的瞬时断连。"""

        last_error: BaseException | None = None
        for attempt in range(1, 4):
            try:
                return self._remote_connect_once(request_packet)
            except (OSError, ConnectionError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(0.35 * attempt)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _relay(client: socket.socket, remote: socket.socket) -> None:
        client.settimeout(None)
        remote.settimeout(None)
        sockets = [client, remote]
        while True:
            readable, _, exceptional = select.select(sockets, [], sockets, 60)
            if exceptional or not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                destination = remote if source is client else client
                destination.sendall(data)

    def handle(self) -> None:
        client = self.request
        remote: socket.socket | None = None
        try:
            version, method_count = _recv_exact(client, 2)
            if version != 5:
                return
            _recv_exact(client, method_count)
            client.sendall(b"\x05\x00")
            header = _recv_exact(client, 4)
            if header[0] != 5 or header[1] != 1:
                client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            address = _read_address(client, header[3])
            port = _recv_exact(client, 2)
            request_packet = header + address + port
            remote, response = self._remote_connect(request_packet)
            client.sendall(response)
            self._relay(client, remote)
        except (OSError, ValueError, ConnectionError) as exc:
            self.server.last_error = f"{type(exc).__name__}: {exc}"
            try:
                client.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
            except OSError:
                pass
        finally:
            if remote:
                try:
                    remote.close()
                except OSError:
                    pass


@dataclass(slots=True, frozen=True)
class RemoteSocks:
    host: str
    port: int
    username: str
    password: str


class AuthenticatedSocksBridge:
    """本地无认证 SOCKS5 到远端账号密码 SOCKS5 的 TCP 桥。"""

    def __init__(self, remote: RemoteSocks) -> None:
        self.remote = remote
        self.server: _BridgeServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server.server_address[1]) if self.server else 0

    def start(self) -> str:
        if self.server:
            return f"socks5://127.0.0.1:{self.port}"
        self.server = _BridgeServer(("127.0.0.1", 0), self.remote)
        self.thread = threading.Thread(target=self.server.serve_forever, name=f"socks-bridge-{self.port}", daemon=True)
        self.thread.start()
        return f"socks5://127.0.0.1:{self.port}"

    def stop(self) -> None:
        server, self.server = self.server, None
        thread, self.thread = self.thread, None
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=5)

    def __enter__(self) -> "AuthenticatedSocksBridge":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()
