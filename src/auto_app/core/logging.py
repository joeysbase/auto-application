import logging
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=FORMAT, datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "asyncio", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def add_file_log(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter(FORMAT))
    logging.getLogger().addHandler(handler)
    return handler
