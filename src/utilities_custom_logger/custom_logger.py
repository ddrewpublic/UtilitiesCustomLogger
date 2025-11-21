"""
custom_logger.py

Rich + file-based logging setup utility for CLI-based data processing workflows.
Supports config-driven base paths and multiple output files.

Author: Daniel Drew
"""

import logging
import sys
import traceback
from rich.logging import RichHandler
from rich.console import Console
from pathlib import Path
from typing import Optional

# --- version metadata -------------------------------------------------------
# comment: compat import for Python 3.7–3.10+
try:
    # Python 3.8+
    from importlib.metadata import PackageNotFoundError, version as _pkg_version  # noqa
except ImportError:  # Python < 3.8
    from importlib_metadata import (  # type: ignore[import]
        PackageNotFoundError,
        version as _pkg_version,
    )

# prevent multiple installations across repeated setup_logger calls
_EXC_HOOKS_INSTALLED = False
_ORIG_SYS_EXCEPTHOOK = None
_ORIG_THREADING_EXCEPTHOOK = None
_ORIG_ASYNCIO_HANDLER = None

_PACKAGE_NAME = "utilities_custom_logger"  # matches pyproject.toml
# Optional fallback module-defined version if metadata not available
__version__ = "0.3.0"


def get_logger_version() -> str:
    """Return the version string for this logging utility.

    Resolution order:
        1. Installed package metadata for `utilities_custom_logger`
           (from pyproject.toml).
        2. Local ``__version__`` constant as a fallback.

    Returns:
        str: Semantic version string, e.g. "0.3.0".
    """
    try:
        return _pkg_version(_PACKAGE_NAME)
    except PackageNotFoundError:
        # Not installed as a package – fall back to local constant.
        return __version__
    except Exception:
        # Defensive: never let version lookup break logging.
        return __version__


# --- add near other logging helpers in custom_logger.py ---
class AlignedFileFormatter(logging.Formatter):
    """
    File formatter that:
      - puts source (pathname:lineno) on the first line only, aligned to a fixed column
      - indents continuation lines with a single leading TAB
    """

    def __init__(self, *, datefmt: str = "%Y-%m-%d %H:%M:%S", message_col: int = 32, source_col: int = 220):
        super().__init__(datefmt=datefmt)
        self.message_col: int = message_col  # where the *message* should start
        self.source_col: int = source_col  # where the source should start (first line only)

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        lvl = record.levelname
        src = f"{record.filename}:{record.lineno}"

        # prefix up to (but not including) the message
        prefix = f"{ts} [{lvl}] "
        # compute padding so message starts at message_col
        pad_to_msg = max(1, self.message_col - len(prefix))
        msg_lines = (record.getMessage() or "").splitlines() or [""]

        # first line left part (prefix + spaces + first msg line)
        left_first = f"{prefix}{' ' * pad_to_msg}{msg_lines[0]}"

        # add spacing so source begins at source_col (at least 1 space gap)
        pad_to_src = max(1, self.source_col - len(left_first) - len(src))
        first_line = f"{left_first}{' ' * pad_to_src}{src}"

        # continuation lines: start exactly at message_col with 4 spaces before text if you prefer
        cont_prefix = " " * self.message_col
        cont_lines = [f"{cont_prefix}{line}" for line in msg_lines[1:]]

        return "\n".join([first_line] + cont_lines)


# hook uncaught exceptions into the configured logger
def _install_exception_logging(logger: logging.Logger) -> None:
    """
    Install process-wide hooks that route *uncaught* exceptions to `logger`.

    What this does:
      • sys.excepthook: logs uncaught exceptions from the main thread (and threads
        without custom handling) with the full formatted traceback embedded in the
        log message text.
      • threading.excepthook (Py ≥3.8): logs uncaught exceptions from worker threads,
        including the thread name, with full traceback text.
      • asyncio exception handler (if an event loop exists now): logs unhandled
        exceptions and context errors originating from the active loop.

    Design notes:
      • Idempotent: safe to call multiple times; hooks are installed once per process.
      • Terminal behavior preserved: after logging, the original hooks/handlers are
        invoked so the default/pretty traceback still appears in the terminal.
      • We *embed* the formatted traceback into the message instead of relying on
        `exc_info`, ensuring file formatters always include it.

    Args:
        logger: A configured `logging.Logger` that already has its handlers set up.
    """
    # Reuse module-level flags/originals to avoid reinstalling.
    global _EXC_HOOKS_INSTALLED, _ORIG_SYS_EXCEPTHOOK, _ORIG_THREADING_EXCEPTHOOK, _ORIG_ASYNCIO_HANDLER
    if _EXC_HOOKS_INSTALLED:
        return

    import sys
    import threading
    import asyncio
    import traceback as _tb

    # Helper: format a full traceback string for any (type, value, tb) triple.
    def _fmt_exc(exc_type, exc, tb) -> str:
        return "".join(_tb.format_exception(exc_type, exc, tb))

    # ---- sys.excepthook (main thread / baseline for others) ----
    _ORIG_SYS_EXCEPTHOOK = sys.excepthook

    def _sys_hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            # Preserve Ctrl-C behavior; do not log.
            return _ORIG_SYS_EXCEPTHOOK(exc_type, exc, tb)
        logger.error("Uncaught exception\n%s", _fmt_exc(exc_type, exc, tb))
        # Still print the default traceback to the terminal.
        return _ORIG_SYS_EXCEPTHOOK(exc_type, exc, tb)

    sys.excepthook = _sys_hook

    # ---- threading.excepthook (Python 3.8+) ----
    _ORIG_THREADING_EXCEPTHOOK = getattr(threading, "excepthook", None)

    if _ORIG_THREADING_EXCEPTHOOK is not None:
        def _thread_hook(args: "threading.ExceptHookArgs"):
            if issubclass(args.exc_type, KeyboardInterrupt):
                return
            logger.error(
                "Uncaught thread exception (%s)\n%s",
                getattr(args.thread, "name", "unknown"),
                _fmt_exc(args.exc_type, args.exc_value, args.exc_traceback),
            )
            # Delegate to the original thread hook (if it isn’t ourselves).
            if _ORIG_THREADING_EXCEPTHOOK is not _thread_hook:
                _ORIG_THREADING_EXCEPTHOOK(args)

        threading.excepthook = _thread_hook

    # ---- asyncio loop exceptions (only if a loop exists now) ----
    _ORIG_ASYNCIO_HANDLER = None
    try:
        loop = asyncio.get_event_loop()
        if loop and not loop.is_closed():
            _ORIG_ASYNCIO_HANDLER = loop.get_exception_handler()

            def _asyncio_handler(loop, context):
                exc = context.get("exception")
                if exc is not None:
                    # exc.__traceback__ may be None in rare cases; format_exception handles it.
                    logger.error(
                        "Unhandled asyncio exception\n%s",
                        "".join(_tb.format_exception(type(exc), exc, exc.__traceback__)),
                    )
                else:
                    # No exception object; include context message/details.
                    logger.error("Unhandled asyncio error: %s", context.get("message", context))
                # Delegate to original/default handler so terminal output remains.
                if _ORIG_ASYNCIO_HANDLER:
                    _ORIG_ASYNCIO_HANDLER(loop, context)
                else:
                    loop.default_exception_handler(context)

            loop.set_exception_handler(_asyncio_handler)
    except Exception:
        # No active loop available at install time (common for non-async CLIs) — that's fine.
        pass

    _EXC_HOOKS_INSTALLED = True


def setup_logger(log_file: Optional[Path] = None,
                 error_log_file: Optional[Path] = None,
                 level: str = "INFO",
                 overwrite: bool = True,
                 width: int = 220,
                 exceptions: bool = True,  # <--- new
                 ) -> logging.Logger:
    """
    Configure a logger with rich formatting, full file output, and error-specific file.
    If install_exception_hooks is True, uncaught exceptions are logged by default.
    Args:
        log_file (Optional[Path]): File path for general logs.
        error_log_file (Optional[Path]): File path for ERROR+ logs.
        level (str): Logging level threshold (e.g., 'DEBUG', 'INFO').
        overwrite (bool): Flag to overwrite append or file if exists.
        width (int): Width of each log line.
        exceptions (bool): Log uncaught exceptions, defaults to True.

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger("config_loader_logger")
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(level.upper())

    # Standard formatter for files AND console (aligned columns)
    file_fmt = AlignedFileFormatter(
        datefmt="%Y-%m-%d %H:%M:%S",
        message_col=32,
        source_col=width,
    )

    # Console handler with Rich, but using our aligned formatter
    rich_handler = RichHandler(
        console=Console(file=sys.stdout),  # stdout as before
        rich_tracebacks=True,
        # let our formatter handle time/level/path so alignment matches the file
        show_time=False,
        show_level=False,
        show_path=False,
        log_time_format="%Y-%m-%d %H:%M:%S",
        omit_repeated_times=False,
    )
    rich_handler.setFormatter(file_fmt)
    logger.addHandler(rich_handler)

    if log_file:
        log_file = Path(log_file).resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file,
                                           mode=("w" if overwrite else "a"),
                                           encoding="utf-8")
        file_handler.setLevel(level.upper())
        file_handler.setFormatter(file_fmt)
        # file_handler.addFilter(IndentMultilineFilterTabs())
        logger.addHandler(file_handler)

    # ---- Optional dedicated ERROR+ file ----
    if error_log_file:
        err_path = Path(error_log_file).resolve()
        std_path = Path(log_file).resolve() if log_file else None

        # Only add a separate ERROR handler if it's a different file than the standard log
        if std_path is None or err_path != std_path:
            err_path.parent.mkdir(parents=True, exist_ok=True)
            err_handler = logging.FileHandler(err_path, mode=("w" if overwrite else "a"), encoding="utf-8")
            err_handler.setLevel(logging.ERROR)
            err_handler.setFormatter(file_fmt)
            logger.addHandler(err_handler)
    else:
        # No error file provided:
        # fall back to sending ERROR+ messages to the standard log (handled by the general file handler if present)
        pass  # (no extra handler needed; errors will already flow to console and to log_file if set)

    logger.propagate = False

    if exceptions:
        _install_exception_logging(logger)
    return logger


def main():
    return True


if __name__ == "__main__":
    main()
