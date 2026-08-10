from __future__ import annotations

import threading
import unittest

from cloudflare_handler import wait_cloudflare_interactive


class FakeControl:
    def __init__(self, page: "FakePage", visible: bool) -> None:
        self.page = page
        self.visible = visible
        self.first = self

    def count(self) -> int:
        return int(self.visible)

    def is_visible(self, **_kwargs: object) -> bool:
        return self.visible

    def get_attribute(self, _name: str) -> str:
        return "false"

    def is_checked(self, **_kwargs: object) -> bool:
        return False

    def bounding_box(self, **_kwargs: object) -> dict[str, float]:
        return {"x": 100.0, "y": 100.0, "width": 40.0, "height": 30.0}


class FakeFrame:
    url = "https://challenges.cloudflare.com/turnstile/"

    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def locator(self, selector: str) -> FakeControl:
        return FakeControl(self.page, selector == "input[type='checkbox']")

    def get_by_text(self, *_args: object, **_kwargs: object) -> FakeControl:
        return FakeControl(self.page, False)

    def frame_element(self) -> FakeControl:
        return FakeControl(self.page, True)


class FakeMouse:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def move(self, *_args: object, **_kwargs: object) -> None:
        pass

    def down(self) -> None:
        pass

    def up(self) -> None:
        self.page.challenge = False


class FakePage:
    url = "https://example.test/"

    def __init__(self) -> None:
        self.challenge = True
        self.mouse = FakeMouse(self)
        self.frames = [FakeFrame(self)]

    def title(self) -> str:
        return "Just a moment..." if self.challenge else "Finished"

    def evaluate(self, _script: str, *_args: object) -> str:
        return "Cloudflare Verify you are human" if self.challenge else "normal page"

    def locator(self, _selector: str) -> FakeControl:
        return FakeControl(self, False)

    def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


class CloudflareHandlerTests(unittest.TestCase):
    def test_late_widget_poll_click_and_pass(self) -> None:
        page = FakePage()
        logs: list[str] = []
        self.assertTrue(wait_cloudflare_interactive(page, 2, logs.append, threading.Event()))
        self.assertFalse(page.challenge)
        self.assertTrue(any("真实点击" in line for line in logs))
        self.assertTrue(any("已放行" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
