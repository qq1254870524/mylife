from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from socks_bridge import AuthenticatedSocksBridge, RemoteSocks

LogFn = Callable[[str], None]


@dataclass(slots=True, frozen=True)
class ProxySpec:
    number: int
    host: str
    port: int
    username: str
    password: str
    refresh_url: str

    @property
    def label(self) -> str:
        return f"代理{self.number}"

    @property
    def requests_url(self) -> str:
        from urllib.parse import quote

        return f"socks5h://{quote(self.username, safe='')}:{quote(self.password, safe='')}@{self.host}:{self.port}"

@dataclass(slots=True)
class ProxyGeo:
    public_ip: str = ""
    timezone: str = "America/New_York"
    locale: str = "en-US"
    latitude: float | None = None
    longitude: float | None = None


def parse_proxy_line(line: str, number: int = 1) -> ProxySpec:
    raw = str(line or "").strip()
    left, separator, refresh_url = raw.partition("|")
    if not separator or not refresh_url.strip():
        raise ValueError("代理格式必须是 host:port:user:password|刷新链接")
    parts = left.split(":", 3)
    if len(parts) != 4:
        raise ValueError("代理格式必须包含 host、port、user、password")
    host, port_text, username, password = (x.strip() for x in parts)
    if not host or not username or not password:
        raise ValueError("代理 host、user、password 不可为空")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or any(ch.isspace() for ch in username + password):
        raise ValueError("代理 host、user、password 格式无效")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("代理端口必须是数字") from exc
    if not 1 <= port <= 65535:
        raise ValueError("代理端口超出范围")
    if not re.match(r"^https?://", refresh_url.strip(), re.I):
        raise ValueError("刷新链接必须是 http/https URL")
    return ProxySpec(number, host, port, username, password, refresh_url.strip())


def load_proxy_lines_from_design(path: str | Path) -> list[str]:
    design = Path(path)
    if not design.exists():
        return []
    lines: list[str] = []
    pattern = re.compile(
        r"(?P<proxy>[A-Za-z0-9.-]+:\d{1,5}:[^:\s|]+:[^\s|]+\|https?://[A-Za-z0-9:/?&=._%+\-#]+)"
    )
    for line in design.read_text(encoding="utf-8").splitlines():
        for match in pattern.finditer(line):
            candidate = match.group("proxy")
            try:
                parse_proxy_line(candidate, len(lines) + 1)
            except ValueError:
                continue
            if candidate not in lines:
                lines.append(candidate)
    return lines


class ProxyBridge:
    """把带认证的 SOCKS5 转成 Chromium 可用的本地无认证 SOCKS5。"""

    def __init__(self, spec: ProxySpec, log: LogFn | None = None) -> None:
        self.spec = spec
        self.log = log or (lambda _message: None)
        self.port = 0
        self.bridge: AuthenticatedSocksBridge | None = None

    def start(self) -> str:
        if self.bridge:
            return f"socks5://127.0.0.1:{self.port}"
        self.bridge = AuthenticatedSocksBridge(
            RemoteSocks(self.spec.host, self.spec.port, self.spec.username, self.spec.password)
        )
        url = self.bridge.start()
        self.port = self.bridge.port
        self.log(f"{self.spec.label} 本地代理桥已就绪")
        return url

    def stop(self) -> None:
        bridge, self.bridge = self.bridge, None
        if bridge:
            bridge.stop()

    def __enter__(self) -> "ProxyBridge":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


class ProxyPool:
    CHECK_URLS = ("https://api.ipify.org", "https://checkip.amazonaws.com")

    def __init__(
        self,
        specs: list[ProxySpec],
        state_path: Path,
        log: LogFn | None = None,
        refresh_cooldown: int = 180,
    ) -> None:
        self.specs = specs
        self.state_path = state_path
        self.log = log or (lambda _message: None)
        self.refresh_cooldown = refresh_cooldown
        self._lock = threading.Lock()
        self._refresh_times = self._load_state()

    def _load_state(self) -> dict[str, float]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {str(key): float(value) for key, value in data.items()}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._refresh_times, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.state_path)

    @staticmethod
    def _key(spec: ProxySpec) -> str:
        return f"{spec.host}:{spec.port}:{spec.username}"

    def check(self, spec: ProxySpec, timeout: int = 15) -> ProxyGeo | None:
        proxies = {"http": spec.requests_url, "https": spec.requests_url}
        session = requests.Session()
        session.trust_env = False
        public_ip = ""
        for url in self.CHECK_URLS:
            try:
                response = session.get(url, proxies=proxies, timeout=timeout)
                response.raise_for_status()
                public_ip = response.text.strip()
                if public_ip:
                    break
            except (requests.RequestException, ValueError):
                continue
        if not public_ip:
            self.log(f"{spec.label} 连通性检查失败")
            return None
        geo = ProxyGeo(public_ip=public_ip)
        try:
            response = session.get("https://ipapi.co/json/", proxies=proxies, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            timezone = str(data.get("timezone") or "").strip()
            country = str(data.get("country_code") or "US").upper()
            if timezone:
                geo.timezone = timezone
            geo.locale = {"US": "en-US", "CA": "en-CA", "GB": "en-GB", "AU": "en-AU"}.get(country, "en-US")
            if data.get("latitude") is not None and data.get("longitude") is not None:
                geo.latitude = float(data["latitude"])
                geo.longitude = float(data["longitude"])
        except (requests.RequestException, ValueError, TypeError):
            pass
        self.log(f"{spec.label} 连通性正常，出口 IP 已确认，时区={geo.timezone}")
        return geo

    def refresh(self, spec: ProxySpec) -> bool:
        key = self._key(spec)
        with self._lock:
            now = time.time()
            elapsed = now - self._refresh_times.get(key, 0.0)
            if elapsed < self.refresh_cooldown:
                remain = int(self.refresh_cooldown - elapsed + 0.999)
                self.log(f"{spec.label} 刷新冷却中，剩余 {remain} 秒")
                return False
            try:
                response = requests.get(spec.refresh_url, timeout=30)
                response.raise_for_status()
            except requests.RequestException as exc:
                self.log(f"{spec.label} 刷新请求失败：{type(exc).__name__}")
                return False
            self._refresh_times[key] = now
            self._save_state()
            self.log(f"{spec.label} 已发送刷新 IP 请求，等待 10 秒")
            return True

    def ensure_ready(self, spec: ProxySpec, cancel: threading.Event, attempts: int = 5) -> ProxyGeo | None:
        geo = self.check(spec)
        if geo:
            return geo
        refreshed = self.refresh(spec)
        if not refreshed:
            return None
        for attempt in range(1, attempts + 1):
            if cancel.wait(10):
                return None
            self.log(f"{spec.label} 刷新后第 {attempt} 次连通性复查")
            geo = self.check(spec)
            if geo:
                return geo
        return None

    def refresh_after_challenge(self, spec: ProxySpec, cancel: threading.Event) -> ProxyGeo | None:
        if not self.refresh(spec):
            return None
        for _ in range(5):
            if cancel.wait(10):
                return None
            geo = self.check(spec)
            if geo:
                return geo
        return None

    def wait_until_ready(
        self,
        spec: ProxySpec,
        cancel: threading.Event,
        check_interval: float = 10.0,
    ) -> ProxyGeo | None:
        """保持工作线程存活，循环检测/刷新当前专属代理直到恢复或收到停止指令。"""

        cycle = 0
        while not cancel.is_set():
            cycle += 1
            geo = self.check(spec, timeout=10)
            if geo:
                if cycle > 1:
                    self.log(f"{spec.label} 已恢复，浏览器线程继续工作")
                return geo
            refreshed = self.refresh(spec)
            wait_seconds = 10.0 if refreshed else check_interval
            self.log(
                f"{spec.label} 暂不可用，浏览器已关闭但工作线程保持；"
                f"{wait_seconds:g} 秒后继续检测"
            )
            if cancel.wait(wait_seconds):
                return None
        return None


def cleanup_profile_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
