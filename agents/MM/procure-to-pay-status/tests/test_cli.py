from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest

from procure_to_pay_status.cli import main


class CliTests(unittest.TestCase):
    def test_json_end_to_end(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main(["PO 4500001234 item 50", "--as-of", "2026-07-22", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["items"][0]["status"], "paid")

    def test_bad_question_returns_nonzero(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main(["请查一下付款状态"])
        self.assertEqual(code, 2)
        self.assertIn("未识别", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

