from pathlib import Path


def demo_path() -> Path:
    return Path(__file__).with_name("fixtures") / "demo.json"
