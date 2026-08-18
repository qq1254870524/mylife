"""MyLife HTTP 查询后端。

2026-08-17：参考本机 USFull 的会话持久化、StudentAid 的独立 HTTP worker，
以及 TXFGSales 的浏览器/HTTP 接力结构新增。每个 worker 仅在首次或会话失效时用
Patchright 完成 Cloudflare 会话初始化，之后搜索分页和详情读取复用独立 HTTP 会话；
HTTP 会话被拒绝时重新同步浏览器 Cookie，并保留浏览器文档兜底。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from browser_worker import BrowserSession, Cancelled, CloudflareFailure
from models import ProfileResult, SearchResult
from mylife_parser import parse_profile_html, parse_search_results


HTTP_TIMEOUT = (15.0, 35.0)
HTTP_USER_AGENT_FALLBACK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)
SessionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class HttpDocument:
    url: str
    html: str
    status_code: int
    source: str


class HttpInterfaceSession(BrowserSession):
    """浏览器初始化 Cloudflare，搜索和详情优先走 requests 的混合 worker。"""

    def __init__(self, *args: Any, session_factory: SessionFactory | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session_factory = session_factory or requests.Session
        self._http_session: Any = None
        self._bootstrap_document: HttpDocument | None = None
        self._http_request_count = 0

    def _close_http_session(self) -> None:
        session, self._http_session = self._http_session, None
        self._bootstrap_document = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def start(self, fresh_profile: bool = False) -> None:
        self._close_http_session()
        super().start(fresh_profile=fresh_profile)
        self.log(
            f"线程{self.worker_number} HTTP接口后端已启动；"
            "浏览器只负责 Cloudflare 会话初始化和兼容兜底"
        )

    def close(self) -> None:
        self._close_http_session()
        super().close()

    def clear_person_data(self) -> None:
        """清空当前页面和缓存，但保留同 worker 的 Cloudflare/HTTP 会话。"""

        if not self.context or not self.page:
            return
        try:
            self.page.goto("about:blank", wait_until="commit", timeout=15_000)
        except Exception:
            pass
        try:
            self.context.clear_permissions()
        except Exception:
            pass
        try:
            cdp = self.context.new_cdp_session(self.page)
            cdp.send("Network.clearBrowserCache")
            cdp.detach()
        except Exception:
            pass

    def _new_http_session(self) -> Any:
        if not self.context or not self.page:
            raise RuntimeError("HTTP接口后端缺少浏览器会话")
        session = self._session_factory()
        if hasattr(session, "trust_env"):
            session.trust_env = False
        try:
            user_agent = str(self.page.evaluate("() => navigator.userAgent") or "").strip()
        except Exception:
            user_agent = ""
        session.headers.update({
            "User-Agent": user_agent or HTTP_USER_AGENT_FALLBACK,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.mylife.com/",
        })
        for item in self.context.cookies():
            name = str(item.get("name", "") or "")
            if not name:
                continue
            session.cookies.set(
                name,
                str(item.get("value", "") or ""),
                domain=str(item.get("domain", "") or "") or None,
                path=str(item.get("path", "/") or "/"),
            )
        if self.proxy_spec:
            proxy = self.proxy_spec.requests_url
            session.proxies.update({"http": proxy, "https": proxy})
        return session

    @staticmethod
    def _html_metadata(html: str) -> tuple[str, str]:
        soup = BeautifulSoup(html or "", "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        body = soup.get_text(" ", strip=True)
        return title, body

    @staticmethod
    def _blocked(status_code: int, html: str, title: str, body: str) -> bool:
        sample = f"{title} {body[:3000]} {html[:12000]}".casefold()
        return status_code in {401, 403, 429} or any(
            marker in sample
            for marker in (
                "cf-chl-",
                "just a moment",
                "sorry, you have been blocked",
                "attention required",
                "challenge-platform",
            )
        )

    def _bootstrap_http(self, url: str) -> None:
        self._check_cancelled()
        if not self.page:
            self.start(fresh_profile=False)
        self.log(f"线程{self.worker_number} 正在用浏览器初始化 HTTP接口会话")
        self._navigate(url)
        current_url = str(self.page.url or url)
        html = str(self.page.content() or "")
        title, body = self._html_metadata(html)
        if not self._is_usable_mylife_page(current_url, html, body, title):
            raise CloudflareFailure("浏览器完成验证后仍未取得可复用的 MyLife 页面")
        old_session, self._http_session = self._http_session, None
        if old_session is not None:
            try:
                old_session.close()
            except Exception:
                pass
        self._http_session = self._new_http_session()
        self._bootstrap_document = HttpDocument(current_url, html, 200, "浏览器会话初始化")
        cookie_count = len(getattr(self._http_session, "cookies", ()))
        self.log(
            f"线程{self.worker_number} HTTP接口会话初始化完成："
            f"Cookie={cookie_count}，后续搜索和详情优先走 HTTP"
        )

    def _fallback_document(self, url: str) -> HttpDocument | None:
        document = self._bootstrap_document
        if document is None:
            return None
        requested_path = urlsplit(url).path.rstrip("/").casefold()
        actual_path = urlsplit(document.url).path.rstrip("/").casefold()
        return document if requested_path == actual_path else None

    def _fetch_html(self, url: str) -> HttpDocument:
        self._check_cancelled()
        if self._http_session is None:
            self._bootstrap_http(url)
            document = self._fallback_document(url)
            if document is not None:
                return document

        last_error: BaseException | None = None
        for attempt in range(2):
            self._check_cancelled()
            try:
                response = self._http_session.get(
                    url,
                    timeout=HTTP_TIMEOUT,
                    allow_redirects=True,
                )
                self._http_request_count += 1
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    self.log(
                        f"线程{self.worker_number} HTTP请求异常，正在重新同步浏览器会话："
                        f"{type(exc).__name__}"
                    )
                    self._bootstrap_http(url)
                    continue
                document = self._fallback_document(url)
                if document is not None:
                    self.log(
                        f"线程{self.worker_number} HTTP请求重试仍异常，"
                        "本次使用同会话浏览器文档兼容兜底"
                    )
                    return HttpDocument(
                        document.url,
                        document.html,
                        document.status_code,
                        "浏览器兼容兜底",
                    )
                marker = "ERR_PROXY_CONNECTION_FAILED: " if self.proxy_spec else ""
                raise RuntimeError(
                    f"{marker}HTTP接口请求失败：{type(exc).__name__}: {exc}"
                ) from exc

            status_code = int(getattr(response, "status_code", 0) or 0)
            final_url = str(getattr(response, "url", "") or url)
            html = str(getattr(response, "text", "") or "")
            host = (urlsplit(final_url).hostname or "").casefold()
            title, body = self._html_metadata(html)
            blocked = self._blocked(status_code, html, title, body)
            usable = host in {"mylife.com", "www.mylife.com"} and self._is_usable_mylife_page(
                final_url, html, body, title
            )
            if 200 <= status_code < 300 and usable and not blocked:
                self._bootstrap_document = None
                return HttpDocument(final_url, html, status_code, "HTTP接口")

            if blocked and attempt == 0:
                self.log(
                    f"线程{self.worker_number} HTTP接口会话失效（HTTP {status_code}），"
                    "正在自动重新初始化"
                )
                self._bootstrap_http(url)
                continue
            if status_code >= 500:
                raise RuntimeError(f"HTTP接口服务端错误（HTTP {status_code}）")
            last_error = RuntimeError(
                f"HTTP接口返回不可解析页面（HTTP {status_code}，host={host or '空'}）"
            )
            break

        document = self._fallback_document(url)
        if document is not None:
            self.log(
                f"线程{self.worker_number} HTTP接口重试后仍不可用，"
                "本次使用同会话浏览器文档兼容兜底"
            )
            return HttpDocument(document.url, document.html, document.status_code, "浏览器兼容兜底")
        if isinstance(last_error, Cancelled):
            raise last_error
        if isinstance(last_error, CloudflareFailure):
            raise last_error
        raise CloudflareFailure(str(last_error or "HTTP接口会话不可用"))

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
            document = self._fetch_html(url)
            results, next_url, no_results = parse_search_results(document.html, document.url)
            self.log(
                f"线程{self.worker_number} HTTP搜索结果第 {page_number} 页："
                f"{len(results)} 条，来源={document.source}"
            )
            for item in results:
                if item.profile_url not in seen_profiles:
                    seen_profiles.add(item.profile_url)
                    all_results.append(item)
            if no_results or not next_url:
                break
            url = next_url
        return all_results

    def _collect_profile(
        self,
        result: SearchResult,
        index: int,
        strategy: str,
        search_url: str,
    ) -> ProfileResult:
        del search_url
        self._check_cancelled()
        document = self._fetch_html(result.profile_url)
        parsed = parse_profile_html(document.html, result, index, strategy)
        self.log(
            f"线程{self.worker_number} HTTP接口已采集第 {index} 个详情，"
            f"生日={'已提取' if parsed.birthday else '页面未公开'}，来源={document.source}"
        )
        return parsed
