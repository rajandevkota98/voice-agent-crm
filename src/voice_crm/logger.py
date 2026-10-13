"""Project logging. Every module logs through the `voice_crm` logger tree."""

import logging
import sys

ROOT_NAME = "voice_crm"
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(level="INFO", log_file=None):
    root = logging.getLogger(ROOT_NAME)
    if root.handlers:
        return root

    formatter = logging.Formatter(_FORMAT)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(level)
    root.propagate = False
    return root


def get_logger(part):
    return logging.getLogger(f"{ROOT_NAME}.{part}")
