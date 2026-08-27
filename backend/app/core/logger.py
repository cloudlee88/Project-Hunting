import logging
import sys

_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_root = logging.getLogger()
if not getattr(_root, "_ah_configured", False):
    _root.setLevel(logging.INFO)
    _formatter = logging.Formatter(_FMT)

    _stream = logging.StreamHandler(sys.stdout)
    _stream.setFormatter(_formatter)
    _root.addHandler(_stream)

    _root._ah_configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
