from __future__ import annotations
from typing import Sequence
from sd_o2c_shared import run_cli
from . import DEFAULT_FIXTURE, SLUG

def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(SLUG, DEFAULT_FIXTURE, argv)
