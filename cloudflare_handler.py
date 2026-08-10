from __future__ import annotations

import random
import re
import threading
import time
from typing import Any, Callable

from turnstile_harvester import page_is_cloudflare_challenge

LogFn = Callable[[str], None]


class ChallengePageAdapter:
    """把 Patchright Page 适配为持续、真实鼠标操作的 Cloudflare 控件处理器。"""

    def __init__(self, page: Any) -> None:
        self.page = page
        self._last_click_at = 0.0
        self.click_count = 0
        self.last_detail: dict[str, object] = {}

    @property
    def url(self) -> str:
        try:
            return str(self.page.url or "")
        except Exception:
            return ""

    @property
    def title(self) -> str:
        try:
            return str(self.page.title() or "")
        except Exception:
            return ""

    def run_js(self, script: str, *args: object) -> object:
        source = str(script).replace("arguments[0]", "value")
        if args:
            return self.page.evaluate(f"(value) => {{ {source} }}", args[0])
        return self.page.evaluate(f"() => {{ {source} }}")

    def ele(self, _selector: str) -> None:
        return None

    def _mouse_click(self, x: float, y: float, method: str, selector: str, frame_url: str = "") -> bool:
        self.page.mouse.move(x + random.uniform(-20, 20), y + random.uniform(-10, 10))
        self.page.wait_for_timeout(random.randint(140, 360))
        self.page.mouse.move(x, y, steps=random.randint(4, 9))
        self.page.wait_for_timeout(random.randint(70, 180))
        self.page.mouse.down()
        self.page.wait_for_timeout(random.randint(80, 160))
        self.page.mouse.up()
        self.click_count += 1
        self.last_detail = {
            "clicked": True,
            "method": method,
            "selector": selector,
            "target_x": round(x, 1),
            "target_y": round(y, 1),
            "frame_url": frame_url,
            "click_count": self.click_count,
        }
        return True

    def click_turnstile_widget(self) -> bool:
        now = time.monotonic()
        if now - self._last_click_at < 1.6:
            return False
        self._last_click_at = now
        errors: list[str] = []
        selectors = (
            "input[type='checkbox']",
            "[role='checkbox']",
            "label:has(input[type='checkbox'])",
            ".ctp-checkbox-label",
            ".ctp-checkbox",
            ".cf-turnstile",
            "[data-sitekey]",
            "[id^='cf-chl-widget']",
            "[class*='turnstile' i]",
        )
        frames = tuple(getattr(self.page, "frames", ()) or ())
        frame_urls: list[str] = []
        containers = {".cf-turnstile", "[data-sitekey]", "[id^='cf-chl-widget']", "[class*='turnstile' i]"}
        for frame_index, frame in enumerate(frames):
            try:
                frame_url = str(getattr(frame, "url", "") or "")
            except Exception:
                frame_url = ""
            frame_urls.append(frame_url[:240])
            for selector in selectors:
                try:
                    control = frame.locator(selector).first
                    if not control.count() or not control.is_visible(timeout=350):
                        continue
                    try:
                        if str(control.get_attribute("aria-checked") or "").casefold() == "true":
                            continue
                        if selector == "input[type='checkbox']" and control.is_checked(timeout=250):
                            continue
                    except Exception:
                        pass
                    box = control.bounding_box(timeout=800)
                    if not box:
                        continue
                    x = box["x"] + (min(random.uniform(28, 36), box["width"] / 2) if selector in containers else box["width"] / 2 + random.uniform(-2, 2))
                    y = box["y"] + box["height"] / 2 + random.uniform(-1.5, 1.5)
                    return self._mouse_click(x, y, "frame-control-mouse", selector, frame_url)
                except Exception as exc:
                    errors.append(f"frame[{frame_index}] {selector}: {type(exc).__name__}")
            try:
                verify = frame.get_by_text(re.compile(r"verify\s+you\s+are\s+human|验证您是人类", re.I)).first
                if verify.count() and verify.is_visible(timeout=350):
                    box = verify.bounding_box(timeout=800)
                    if box:
                        x = max(8.0, box["x"] - random.uniform(24, 34))
                        y = box["y"] + box["height"] / 2 + random.uniform(-2, 2)
                        return self._mouse_click(x, y, "verify-text-left-coordinate", "text=Verify you are human", frame_url)
            except Exception as exc:
                errors.append(f"frame[{frame_index}] verify-text: {type(exc).__name__}")
            if any(marker in frame_url.casefold() for marker in ("challenges.cloudflare.com", "/cdn-cgi/challenge-platform/", "turnstile")):
                try:
                    element = frame.frame_element()
                    box = element.bounding_box()
                    if box and box["width"] >= 120 and box["height"] >= 35:
                        x = box["x"] + min(random.uniform(28, 36), box["width"] / 2)
                        y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
                        return self._mouse_click(x, y, "challenge-frame-element-coordinate", "frame.frame_element()", frame_url)
                except Exception as exc:
                    errors.append(f"frame[{frame_index}] frame-element: {type(exc).__name__}")
        try:
            iframe = self.page.locator(
                "iframe[src*='challenges.cloudflare.com'], iframe[src*='/cdn-cgi/challenge-platform/'], "
                "iframe[src*='turnstile'], iframe[title*='challenge' i], iframe[title*='turnstile' i]"
            ).first
            if iframe.count() and iframe.is_visible(timeout=700):
                box = iframe.bounding_box(timeout=1000)
                if box:
                    x = box["x"] + min(random.uniform(28, 36), box["width"] / 2)
                    y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
                    return self._mouse_click(x, y, "iframe-coordinate-mouse", "cloudflare-iframe")
        except Exception as exc:
            errors.append(f"iframe: {type(exc).__name__}")
        self.last_detail = {
            "clicked": False,
            "reason": "turnstile_control_not_visible",
            "frame_count": len(frames),
            "frame_urls": frame_urls[-8:],
            "errors": errors[-6:],
        }
        return False


def wait_cloudflare_interactive(
    page: Any,
    timeout: float,
    log: LogFn,
    cancel: threading.Event,
) -> bool:
    """普通挑战期间每 450ms 重查控件；控件晚加载后仍会真实点击。"""

    adapter = ChallengePageAdapter(page)
    if not page_is_cloudflare_challenge(adapter):
        return True
    deadline = time.monotonic() + max(5.0, timeout)
    polls = 0
    last_signature = ""
    log("Cloudflare 挑战处理中：每 450ms 持续检查可见 Turnstile 控件")
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise RuntimeError("cancelled")
        polls += 1
        if not page_is_cloudflare_challenge(adapter):
            log(f"Cloudflare 已放行：检查 {polls} 次，真实点击 {adapter.click_count} 次")
            return True
        clicked = adapter.click_turnstile_widget()
        detail = adapter.last_detail
        signature = f"{detail.get('reason')}|{detail.get('frame_count')}|{detail.get('frame_urls')}"
        if clicked:
            log(
                f"Cloudflare 第 {polls} 次检查已真实点击控件："
                f"方式={detail.get('method')}，选择器={detail.get('selector')}，累计={adapter.click_count}"
            )
        elif signature != last_signature or polls == 1 or polls % 10 == 0:
            log(
                f"Cloudflare 第 {polls} 次检查：控件尚未可点击，"
                f"frame={detail.get('frame_count', 0)}，继续等待"
            )
        last_signature = signature
        try:
            page.wait_for_timeout(450)
        except Exception:
            time.sleep(0.45)
    log(f"Cloudflare 等待超时：检查 {polls} 次，真实点击 {adapter.click_count} 次")
    return False
