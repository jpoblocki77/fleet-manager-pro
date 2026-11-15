"""Fleet Manager Pro - activity logging for troubleshooting.

Writes detailed, human-readable logs to a `logs` folder inside the data folder:

    logs/fleetmanager.log   everything that happens (INFO and up), newest at the bottom
    logs/errors.log         only warnings and errors, so problems are easy to find
    logs/access.log         every web request (page / API): who, what, the result, how long

Each line reads:  2026-11-15 17:25:04  INFO    fmpro.licence    licence check ok (expires 2026-11-22)

Files roll over at ~2 MB and keep 10 back, so they never fill the disk. Purchase keys and anything that looks
like a secret are masked automatically before anything is written, so a buyer can safely send you their logs.

Use it like:
    from . import logs
    logs.get("update").info("downloaded %s (%d bytes)", version, size)
"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

_CONFIGURED = False

# A purchase key (FMP-XXXX-XXXX-XXXX-XXXX) -> keep the first and last group only, so logs can be correlated
# without ever recording a whole, usable key. Build tags (FMPB-...) are fine to keep - they don't unlock anything.
_KEY_RX = re.compile(r"\bFMP-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})\b", re.I)
# Obvious "secret=value" shapes, masked whole.
_SECRET_RX = re.compile(r"(?i)\b(password|passwd|pwd|token|secret|authorization|api[_-]?key|cookie)\b"
                        r"(\s*[=:]\s*)(\S+)")


def _mask_key(m):
    return f"FMP-{m.group(1)}-****-****-{m.group(4)}"


def redact(text):
    text = _KEY_RX.sub(_mask_key, text)
    text = _SECRET_RX.sub(lambda m: f"{m.group(1)}{m.group(2)}****", text)
    return text


def mask_key(key):
    """Mask a single purchase key for logging (safe to call with None)."""
    return redact(str(key or ""))


class _Redactor(logging.Filter):
    """Scrubs secrets out of every record before it reaches a file."""

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg, record.args = redact(msg), ()
        return True


class _OnlyAccess(logging.Filter):
    def filter(self, record):
        return record.name == "fmpro.access"


class _NoAccess(logging.Filter):
    def filter(self, record):
        return record.name != "fmpro.access"


def get(name):
    """A logger in a readable category, e.g. get('licence') -> 'fmpro.licence'."""
    return logging.getLogger("fmpro." + name)


def setup(data_dir, level=logging.INFO):
    """Point Fleet Manager Pro's logs at <data_dir>/logs. Safe to call more than once (only the first wins)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logdir = os.path.join(data_dir, "logs")
    try:
        os.makedirs(logdir, exist_ok=True)
    except OSError:
        _CONFIGURED = True            # can't write logs (locked-down folder) - don't crash the app over it
        return

    root = logging.getLogger("fmpro")
    root.setLevel(level)
    root.propagate = False
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(name)-16s %(message)s", "%Y-%m-%d %H:%M:%S")
    red = _Redactor()

    def rotating(filename, lvl, *extra_filters):
        h = RotatingFileHandler(os.path.join(logdir, filename), maxBytes=2_000_000, backupCount=10, encoding="utf-8")
        h.setLevel(lvl)
        h.setFormatter(fmt)
        h.addFilter(red)
        for f in extra_filters:
            h.addFilter(f)
        root.addHandler(h)

    rotating("fleetmanager.log", level, _NoAccess())     # all activity except the noisy access lines
    rotating("errors.log", logging.WARNING)              # just the problems
    rotating("access.log", logging.INFO, _OnlyAccess())  # every web request

    console = logging.StreamHandler()                    # surface warnings in the program's own window
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)
    console.addFilter(red)
    root.addHandler(console)

    _CONFIGURED = True

    from . import VERSION, FROZEN
    get("app").info("=== Fleet Manager Pro %s starting === (data=%s, packaged=%s)", VERSION, data_dir, FROZEN)
