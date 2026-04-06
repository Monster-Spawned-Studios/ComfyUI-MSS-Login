#!/usr/bin/env python3
"""
Generate API reference and extension API documentation from source.

Run from the project root:
    python scripts/build_docs.py

Delegates to docs/scripts/generate_api_docs.py. Writes:
  - docs/api-reference/endpoints.md   (from routes/*.py)
  - docs/extension-api/nsfw-guard-api.md (from api.py)

Does not import ComfyUI or mss-login at runtime; uses regex and ast only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "docs" / "scripts" / "generate_api_docs.py"


def main() -> None:
    """Build the API reference and extension API documentation from source."""
    os.chdir(ROOT)
    if not GENERATOR.exists():
        print("Generator not found: %s", GENERATOR, file=sys.stderr)
        sys.exit(1)
    result = subprocess.run(
        [sys.executable, str(GENERATOR)],
        cwd=str(ROOT),
        check=True,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
