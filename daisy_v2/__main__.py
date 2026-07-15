from __future__ import annotations

import logging

from .app import DaisyV2
from .config import load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if not settings.token:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.v2.example values into your private .env file.")
    DaisyV2(settings).run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
