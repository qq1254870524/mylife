from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from proxy_pool import ProxyPool, load_proxy_lines_from_design, parse_proxy_line


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


if __name__ == "__main__":
    unittest.main()
