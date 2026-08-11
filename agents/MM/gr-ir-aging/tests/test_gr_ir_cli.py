from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from gr_ir_aging.cli import main


FIXTURE = Path(__file__).parents[1] / "src" / "gr_ir_aging" / "fixtures" / "gr_ir_demo.json"


class CliTests(unittest.TestCase):
    def test_json_cli(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = main([
                "--company-code", "1000", "--key-date", "2026-08-01",
                "--evidence", str(FIXTURE), "--json",
            ])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "complete")
        self.assertTrue(payload["read_only"])


if __name__ == "__main__":
    unittest.main()
