"""Central application log setup and log-file location."""

from __future__ import annotations

from pathlib import Path
import logging


def log_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "logs" / "echoplay.log"


def configure_logging() -> Path:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("echoplay")
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, "baseFilename", None) == str(path) for handler in logger.handlers):
        for backup_index in range(1, 4):
            backup_path = path.with_name(f"{path.name}.{backup_index}")
            if backup_path.exists():
                backup_path.unlink()
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
    return path
