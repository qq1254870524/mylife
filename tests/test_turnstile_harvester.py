from __future__ import annotations

import unittest

from turnstile_harvester import get_turnstile_token, inject_turnstile_value, page_is_cloudflare_challenge


class FakePage:
    url = "https://example.test/"
    title = "Normal"
    frames: list[object] = []

    def __init__(self) -> None:
        self.calls = 0

    def run_js(self, script: str, *args: object) -> object:
        if "document.body" in script:
            return "normal page"
        if "cf-turnstile-response" in script and "return String" in script:
            self.calls += 1
            return "x" * 100
        if args:
            return True
        return ""


class TurnstileHarvesterTests(unittest.TestCase):
    def test_existing_module_token_and_detection(self) -> None:
        page = FakePage()
        self.assertFalse(page_is_cloudflare_challenge(page))
        self.assertEqual(len(get_turnstile_token(page, max_rounds=1, sleep_s=0)), 100)
        self.assertTrue(inject_turnstile_value(page, "x" * 100))


if __name__ == "__main__":
    unittest.main()
