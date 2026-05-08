import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def _get_log_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        return exe_dir / "logs"
    return Path.home() / ".clip_synth" / "logs"


def setup_logger(name: str = "clip_synth", log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if getattr(sys, "frozen", False):
        if log_dir is None:
            log_dir = _get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "clip_synth.log"
        file_handler = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            backupCount=1,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.info(f"日志目录: {log_dir}")

    return logger
