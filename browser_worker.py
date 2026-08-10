from __future__ import annotations

import random
import re
import threading
import time
import sys
import ctypes
from pathlib import Path
from typing import Callable

from models import PersonInput, ProfileResult, SearchResult
from mylife_parser import build_search_url, parse_profile_html, parse_search_results
from proxy_pool import ProxyBridge, ProxyGeo, ProxySpec, cleanup_profile_directory
from cloudflare_handler import wait_cloudflare_interactive
from turnstile_harvester import page_is_cloudflare_challenge

LogFn = Callable[[str], None]


def _chrome_window_handles() -> set[int]:
    if sys.platform != "win32":
        return set()
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        handles: set[int] = set()
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd: int, _param: int) -> bool:
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, len(name))
            if name.value.startswith("Chrome_WidgetWin"):
                handles.add(int(hwnd))
            return True

        user32.EnumWindows(callback, 0)
        return handles
    except Exception:
        return set()


def _hide_new_chrome_windows(previous: set[int], timeout: float = 4.0) -> set[int]:
    hidden: set[int] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for hwnd in _chrome_window_handles() - previous:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
                hidden.add(hwnd)
            except Exception:
                pass
        if hidden:
            break
        time.sleep(0.1)
    return hidden


class Cancelled(RuntimeError):
    pass


class CloudflareFailure(RuntimeError):
    pass


class BrowserSession:
    def __init__(
        self,
        worker_number: int,
        profile_dir: Path,
        mode: str,
        proxy_spec: ProxySpec | None,
        proxy_geo: ProxyGeo | None,
        stop_event: threading.Event,
        log: LogFn,
        max_search_pages: int = 50,
    ) -> None:
        self.worker_number = worker_number
        self.profile_dir = profile_dir
        self.mode = mode
        self.proxy_spec = proxy_spec
        self.proxy_geo = proxy_geo or ProxyGeo()
        self.stop_event = stop_event
        self.log = log
        self.max_search_pages = max_search_pages
        self.playwright = None
        self.context = None
        self.page = None
        self.bridge: ProxyBridge | None = None
        self.challenge_failures = 0
        self._profile_geolocation_applied = False
        self._hidden_windows: set[int] = set()

    def _cancelled(self) -> bool:
        return self.stop_event.is_set()

    def _check_cancelled(self) -> None:
        if self._cancelled():
            raise Cancelled("用户已停止")

    def start(self, fresh_profile: bool = False) -> None:
        self.close()
        if fresh_profile:
            cleanup_profile_directory(self.profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        from patchright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        hide_window = self.mode == "无头"
        previous_windows = _chrome_window_handles() if hide_window else set()
        disabled_features = [
            "LocalNetworkAccessChecks",
            "PrivateNetworkAccessRespectPreflightResults",
            "PrivateNetworkAccessSendPreflights",
        ]
        options: dict[str, object] = {
            "user_data_dir": str(self.profile_dir),
            "channel": "chrome",
            # Patchright 真 headless 会使 Managed Turnstile 拒绝签发 token；无头选项运行
            # 真实 Chrome 后由 Win32 隐藏本会话窗口。
            "headless": False,
            "timezone_id": self.proxy_geo.timezone,
            "accept_downloads": False,
        }
        args = [
            "--disable-features=" + ",".join(disabled_features),
            f"--lang={self.proxy_geo.locale}",
        ]
        if self.mode == "小窗口":
            args += ["--window-size=700,900", f"--window-position={30 + self.worker_number * 35},{30 + self.worker_number * 25}"]
            options["no_viewport"] = True
        else:
            options["viewport"] = {"width": 1365, "height": 768}
            options["screen"] = {"width": 1365, "height": 768}
        options["args"] = args
        if self.proxy_spec:
            self.bridge = ProxyBridge(self.proxy_spec, self.log)
            options["proxy"] = {"server": self.bridge.start()}
        self.context = self.playwright.chromium.launch_persistent_context(**options)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(30_000)
        if hide_window:
            self._hidden_windows = _hide_new_chrome_windows(previous_windows)
            if not self._hidden_windows:
                self.close()
                raise RuntimeError("无头模式未能隐藏 Patchright Chrome 窗口")
        self._profile_geolocation_applied = False
        self.log(f"线程{self.worker_number} 浏览器已启动（{self.mode}）")

    def close(self) -> None:
        context, self.context = self.context, None
        if context:
            try:
                context.close()
            except Exception:
                pass
        playwright, self.playwright = self.playwright, None
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass
        bridge, self.bridge = self.bridge, None
        if bridge:
            bridge.stop()
        self.page = None
        self._hidden_windows.clear()

    def _apply_profile_geolocation(self) -> None:
        if self._profile_geolocation_applied or not self.context:
            return
        if self.proxy_geo.latitude is None or self.proxy_geo.longitude is None:
            self._profile_geolocation_applied = True
            return
        try:
            self.context.set_geolocation(
                {"latitude": self.proxy_geo.latitude, "longitude": self.proxy_geo.longitude}
            )
            self.context.grant_permissions(["geolocation"], origin="https://www.mylife.com")
            self._profile_geolocation_applied = True
        except Exception:
            pass

    def rebuild(self, proxy_geo: ProxyGeo | None = None) -> None:
        if proxy_geo:
            self.proxy_geo = proxy_geo
        self.log(f"线程{self.worker_number} 清理旧浏览器数据并新建浏览器")
        self.start(fresh_profile=True)

    def _pause(self, minimum: float = 0.45, maximum: float = 1.35) -> None:
        deadline = time.monotonic() + random.uniform(minimum, maximum)
        while time.monotonic() < deadline:
            self._check_cancelled()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _human_scroll(self) -> None:
        if not self.page:
            return
        try:
            height = int(self.page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"))
            if height <= 800:
                return
            for _ in range(random.randint(1, 3)):
                self._check_cancelled()
                self.page.mouse.wheel(0, random.randint(180, 520))
                self._pause(0.15, 0.55)
        except Exception:
            return

    def _body_text(self) -> str:
        try:
            return str(self.page.locator("body").inner_text(timeout=10_000) or "")
        except Exception:
            return ""

    def _handle_cloudflare(self) -> None:
        if not page_is_cloudflare_challenge(self.page):
            return
        self.log(f"线程{self.worker_number} 检测到 Cloudflare 验证，等待人工式验证流程")
        if not wait_cloudflare_interactive(
            self.page,
            timeout=90,
            log=self.log,
            cancel=self.stop_event,
        ):
            self.challenge_failures += 1
            raise CloudflareFailure("Cloudflare 验证超时")
        self.page.wait_for_timeout(3500)
        self.challenge_failures = 0
        self._apply_profile_geolocation()

    def _navigate(self, url: str) -> None:
        self._check_cancelled()
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self._pause(1.2, 2.6)
        self._handle_cloudflare()
        self._apply_profile_geolocation()
        body = self._body_text()
        title = str(self.page.title() or "")
        blocked = "sorry, you have been blocked" in body.lower() or "attention required" in title.lower()
        if blocked or (response and response.status == 403 and not body.strip()):
            self.challenge_failures += 1
            raise CloudflareFailure("Cloudflare 拒绝当前出口 IP")
        # 验证通过后的首次响应偶尔只有空壳 HTML；同一已验证上下文重载一次。
        if "pub-multisearch.pubview" in url and len(body.strip()) < 80:
            self.page.reload(wait_until="domcontentloaded", timeout=60_000)
            self._pause(1.5, 3.0)
            self._handle_cloudflare()
        self._human_scroll()

    def clear_person_data(self) -> None:
        if not self.context or not self.page:
            return
        try:
            self.page.goto("about:blank", wait_until="commit", timeout=15_000)
        except Exception:
            pass
        try:
            self.context.clear_cookies()
        except Exception:
            pass
        try:
            self.context.clear_permissions()
        except Exception:
            pass
        try:
            session = self.context.new_cdp_session(self.page)
            session.send("Network.clearBrowserCache")
            session.send("Network.clearBrowserCookies")
            session.detach()
        except Exception:
            pass

    def _collect_search_results(self, search_url: str) -> list[SearchResult]:
        all_results: list[SearchResult] = []
        seen_profiles: set[str] = set()
        seen_pages: set[str] = set()
        url = search_url
        for page_number in range(1, self.max_search_pages + 1):
            self._check_cancelled()
            if not url or url in seen_pages:
                break
            seen_pages.add(url)
            self._navigate(url)
            current_url = self.page.url
            results, next_url, no_results = parse_search_results(self.page.content(), current_url)
            self.log(f"线程{self.worker_number} 搜索结果第 {page_number} 页：{len(results)} 条")
            for item in results:
                if item.profile_url not in seen_profiles:
                    seen_profiles.add(item.profile_url)
                    all_results.append(item)
            if no_results or not next_url:
                break
            url = next_url
            self._pause(0.8, 1.8)
        return all_results

    def _collect_profile(self, result: SearchResult, index: int, strategy: str) -> ProfileResult:
        self._navigate(result.profile_url)
        parsed = parse_profile_html(self.page.content(), result, index, strategy)
        self.log(
            f"线程{self.worker_number} 已采集第 {index} 个详情，"
            f"生日={'已提取' if parsed.birthday else '页面未公开'}"
        )
        return parsed

    def process(self, person: PersonInput) -> list[ProfileResult]:
        if not self.page:
            self.start()
        if person.validation_error:
            return [
                ProfileResult(
                    result_index=0,
                    profile_url="",
                    query_strategy="输入校验",
                    status="输入无效",
                    message=person.validation_error,
                )
            ]
        strategies: list[tuple[str, str]] = []
        if person.location:
            strategies.append(("姓名+城市州邮编", build_search_url(person.first_name, person.last_name, person.location)))
        strategies.append(("姓名", build_search_url(person.first_name, person.last_name)))
        search_results: list[SearchResult] = []
        strategy_used = ""
        for strategy, url in strategies:
            self.log(f"线程{self.worker_number} 开始{strategy}搜索")
            search_results = self._collect_search_results(url)
            if search_results:
                strategy_used = strategy
                break
        if not search_results:
            return [
                ProfileResult(
                    result_index=0,
                    profile_url="",
                    full_name=person.full_name,
                    location=person.location,
                    query_strategy="姓名+城市州邮编→姓名" if person.location else "姓名",
                    status="无结果",
                    message="两级搜索均无可采集结果" if person.location else "姓名搜索无可采集结果",
                )
            ]
        details: list[ProfileResult] = []
        for index, search_result in enumerate(search_results, 1):
            self._check_cancelled()
            try:
                details.append(self._collect_profile(search_result, index, strategy_used))
            except CloudflareFailure:
                raise
            except Exception as exc:
                details.append(
                    ProfileResult(
                        result_index=index,
                        profile_url=search_result.profile_url,
                        full_name=search_result.full_name,
                        age=search_result.age,
                        location=search_result.location,
                        former_names=search_result.former_names,
                        result_summary=search_result.result_summary,
                        query_strategy=strategy_used,
                        status="详情失败",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
        return details
