
from __future__ import annotations

import time
from typing import Any, Callable, Optional

LogFn = Callable[[str], None]


def _log(log: Optional[LogFn], msg: str) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def raise_if_cancelled(cancel_callback: Optional[Callable[[], bool]] = None) -> None:
    if cancel_callback and cancel_callback():
        raise Exception("cancelled")


def _run_js(page: Any, script: str, *args: Any) -> Any:
    """兼容原页面封装与 Playwright/Patchright 的脚本执行接口。"""

    runner = getattr(page, "run_js", None)
    if callable(runner):
        return runner(script, *args)
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        raise AttributeError("page has no run_js/evaluate")
    if args:
        body = script.replace("arguments[0]", "args[0]")
        return evaluate(f"(args) => {{ {body} }}", list(args))
    return evaluate(f"() => {{ {script} }}")


def page_is_cloudflare_challenge(page_obj: Any = None) -> bool:
    """Detect Cloudflare interstitial / challenge page."""
    if page_obj is None:
        return False
    try:
        url = str(getattr(page_obj, "url", "") or "")
    except Exception:
        url = ""
    try:
        title_value = getattr(page_obj, "title", "")
        title = str(title_value() if callable(title_value) else title_value or "")
    except Exception:
        title = ""
    try:
        body = _run_js(
            page_obj,
            "return String((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 500);"
        )
        body = str(body or "")
    except Exception:
        body = ""
    blob = f"{url} {title} {body}".lower()
    markers = [
        "just a moment",
        "attention required",
        "checking your browser",
        "cf-browser-verification",
        "enable javascript and cookies",
        "cloudflare",
        "turnstile",
        "internalcaptcha",
        "verify you are human",
        "verify you're human",
        "请稍候",
        "验证您是真人",
        "正在检查您的浏览器",
    ]
    # Avoid false positive on normal pages that merely mention cloudflare in footer scripts.
    if "sign-up" in blob or "sign-in" in blob:
        hard = [
            "just a moment",
            "attention required",
            "checking your browser",
            "cf-browser-verification",
            "internalcaptcha",
            "verify you are human",
            "verify you're human",
            "请稍候",
            "正在检查您的浏览器",
        ]
        return any(m in blob for m in hard)
    return any(m in blob for m in markers)


def wait_cloudflare_passthrough(
    page: Any,
    timeout: float = 45,
    log: Optional[LogFn] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> bool:
    """Wait until Cloudflare interstitial is gone."""
    deadline = time.time() + max(5.0, float(timeout))
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if not page_is_cloudflare_challenge(page):
            _log(log, "[*] Cloudflare challenge not present / passed")
            return True
        _log(log, "[*] waiting Cloudflare passthrough…")
        time.sleep(0.8)
    _log(log, "[!] Cloudflare passthrough timeout")
    return False


def _click_visible_turnstile_checkbox(page: Any, log: Optional[LogFn] = None) -> bool:
    """点击当前确实可见的 Turnstile checkbox；兼容 Cloudflare 的不透明渲染 iframe。"""

    try:
        frames = list(getattr(page, "frames", ()) or ())
    except Exception:
        return False
    for frame in frames[1:]:
        frame_url = str(getattr(frame, "url", "") or "").lower()
        if "challenges.cloudflare.com" not in frame_url or "/turnstile/" not in frame_url:
            continue
        try:
            checkbox = frame.locator('input[type="checkbox"], [role="checkbox"]')
            if checkbox.count() and checkbox.first.is_visible():
                box = checkbox.first.bounding_box()
                if box:
                    page.mouse.move(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                        steps=7,
                    )
                checkbox.first.click(delay=95)
                _log(log, "[*] Turnstile 出现可访问 checkbox，已模拟人工点击")
                return True

            # Cloudflare 当前版本把可见 checkbox 画在不透明 iframe 中，iframe 内对
            # Playwright 暴露 0 个 input/button/div。仅当 frame 的实际可见尺寸与
            # 300x65 checkbox widget 匹配时，点击它左侧真实 checkbox 的中心。
            body = frame.locator("body")
            box = body.bounding_box() if body.count() else None
            text = str(body.inner_text(timeout=1_000) or "").strip() if body.count() else ""
            if (
                box
                and not text
                and 250 <= float(box["width"]) <= 400
                and 45 <= float(box["height"]) <= 100
            ):
                x = float(box["x"]) + 21.0
                y = float(box["y"]) + float(box["height"]) / 2.0
                page.mouse.move(x, y, steps=7)
                page.mouse.click(x, y, delay=95)
                _log(log, "[*] Turnstile 出现不透明可见 checkbox，已按组件坐标模拟人工点击")
                return True
        except Exception:
            continue
    return False


def get_turnstile_token(
    page: Any,
    log: Optional[LogFn] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
    max_rounds: int = 20,
    sleep_s: float = 0.5,
) -> str:
    """Read a live Turnstile token from the current page context.

    Strategy:
    1) 优先等待 Managed Turnstile 的无感验证自动签发 token；
    2) 读取 input[name=cf-turnstile-response] / turnstile.getResponse()；
    3) 只有 iframe 内真实出现可见 checkbox 时才模拟鼠标点击。

    ``Verifying…`` 是自动风险评估过程，不应重置或盲点 iframe；真实人工操作时
    自动打勾正是 Managed 模式的正常结果。
    """
    if page is None:
        raise Exception("page is None; open target site first")

    interactive_clicked = False
    for i in range(max(1, int(max_rounds))):
        raise_if_cancelled(cancel_callback)
        try:
            token = _run_js(
                page,
                """
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch(e) { return ''; }
"""
            )
            token = str(token or "").strip()
            if len(token) >= 80:
                _log(log, f"[*] Turnstile 已通过，token长度={len(token)}")
                return token
        except Exception as exc:
            _log(log, f"[Debug] Turnstile js read fail: {exc}")

        # 前四轮只被动等待。之后也只有真实 checkbox 可见时才点击，不点击 Verifying…。
        if i >= 4 and not interactive_clicked:
            interactive_clicked = _click_visible_turnstile_checkbox(page, log)
        time.sleep(sleep_s)

    raise Exception("Turnstile 获取 token 失败")


def inject_turnstile_value(page: Any, token: str) -> bool:
    """Write token into cf-turnstile-response input for UI fallback submits."""
    token = str(token or "").strip()
    if not token or page is None:
        return False
    try:
        ok = _run_js(
            page,
            """
const token = String(arguments[0] || '');
const input = document.querySelector('input[name="cf-turnstile-response"]');
if (!input) return false;
const proto = HTMLInputElement.prototype;
const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
if (setter) setter.call(input, token); else input.value = token;
input.dispatchEvent(new Event('input', {bubbles:true}));
input.dispatchEvent(new Event('change', {bubbles:true}));
return String(input.value||'').length >= 80;
""",
            token,
        )
        return bool(ok)
    except Exception:
        return False
