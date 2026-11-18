"""Checks that the contract kit in contract/ is byte for byte the shared one.

    python scripts/check_contract.py

The folder is copied unchanged into every language repository; it is never edited here.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contract"

EXPECTED_SHA256 = {
    "API.es.md": "2faa9d90c31150d13a6c1d134c283e24c6becef0d85534e503c3cb4ca4833b69",
    "API.md": "1827cf01e71acd5fe630663140ebeb81318fea466e6b5b4394682cf4d05f1510",
    "checklist.md": "041c07c7ef2137dd07f5cf890ccf4225d370f87d8983fb3f4cba195c8b4b3276",
    "signature-vectors.json": "a3e9bfc9db9ecbcf94ed421c04fa0f0d349cd9c9358dd64792e44e82a362862c",
}


def check(contract_dir: Path = CONTRACT_DIR) -> list[str]:
    """Returns one message per difference; an empty list means the folder is intact."""
    problems: list[str] = []
    found = {p.name for p in contract_dir.iterdir()} if contract_dir.is_dir() else set()
    for name in sorted(found - EXPECTED_SHA256.keys()):
        problems.append(f"unexpected file: contract/{name}")
    for name, expected in sorted(EXPECTED_SHA256.items()):
        path = contract_dir / name
        if not path.is_file():
            problems.append(f"missing file: contract/{name}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"modified file: contract/{name} (sha256 {actual})")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print("The contract kit must stay identical to the shared one.", file=sys.stderr)
        return 1
    print(f"contract/ is intact ({len(EXPECTED_SHA256)} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
