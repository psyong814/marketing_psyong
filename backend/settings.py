import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def load_settings() -> dict:
    with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None
