from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from proxy_pool import ProxyGeo, ProxyPool, load_proxy_lines_from_design, parse_proxy_line


class ProxyPoolTests(unittest.TestCase):
    def test_parse_combined_proxy_line(self) -> None:
        spec = parse_proxy_line("proxy.example:1080:user:pass|https://refresh.example/change", 3)
        self.assertEqual(spec.label, "代理3")
        self.assertEqual(spec.host, "proxy.example")
        self.assertEqual(spec.port, 1080)
        self.assertIn("socks5h://", spec.requests_url)

    def test_load_lines_from_design(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "设计思路.txt"
            path.write_text(
                "说明中内嵌proxy.example:1080:u:p|https://example/change 后面继续中文\n"
                "proxy.example:1080:u:p|https://example/change\n",
                encoding="utf-8",
            )
            self.assertEqual(len(load_proxy_lines_from_design(path)), 1)

    def test_refresh_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = parse_proxy_line("proxy.example:1080:user:pass|https://refresh.example/change")
            pool = ProxyPool([spec], Path(directory) / "state.json", refresh_cooldown=180)
            response = Mock()
            response.raise_for_status.return_value = None
            with patch("proxy_pool.requests.get", return_value=response) as request:
                self.assertTrue(pool.refresh(spec))
                self.assertFalse(pool.refresh(spec))
                self.assertEqual(request.call_count, 1)

    def test_wait_until_ready_keeps_retrying_instead_of_dropping_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = parse_proxy_line("proxy.example:1080:user:pass|https://refresh.example/change")
            logs: list[str] = []
            pool = ProxyPool([spec], Path(directory) / "state.json", log=logs.append)
            recovered = ProxyGeo(public_ip="203.0.113.10")
            with (
                patch.object(pool, "check", side_effect=[None, None, recovered]) as check,
                patch.object(pool, "refresh", return_value=False) as refresh,
            ):
                result = pool.wait_until_ready(spec, threading.Event(), check_interval=0.001)
            self.assertIs(result, recovered)
            self.assertEqual(check.call_count, 3)
            self.assertEqual(refresh.call_count, 2)
            self.assertTrue(any("线程保持" in message for message in logs))
            self.assertTrue(any("已恢复" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
