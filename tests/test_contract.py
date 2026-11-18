from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_contract  # noqa: E402


class ContractKitTests(unittest.TestCase):
    def test_the_contract_kit_is_unchanged(self) -> None:
        self.assertEqual(check_contract.check(), [])
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_contract.py")],
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())

    def test_the_check_notices_a_changed_an_extra_and_a_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "contract"
            shutil.copytree(ROOT / "contract", copy)
            self.assertEqual(check_contract.check(copy), [])

            with (copy / "API.md").open("ab") as handle:
                handle.write(b"\n")
            (copy / "notes.txt").write_text("extra")
            (copy / "checklist.md").unlink()
            problems = check_contract.check(copy)
        self.assertEqual(len(problems), 3)
        self.assertTrue(any(p.startswith("modified file: contract/API.md") for p in problems))
        self.assertIn("unexpected file: contract/notes.txt", problems)
        self.assertIn("missing file: contract/checklist.md", problems)


if __name__ == "__main__":
    unittest.main()
