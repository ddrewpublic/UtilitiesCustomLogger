# src/utilities_custom_logger/__init__.py
# comment: re-export main public API from the custom_logger module
from .custom_logger import setup_logger, get_logger_version

# comment: public symbols for `from utilities_custom_logger import *`
__all__ = [
    "setup_logger",
    "get_logger_version",
    "__version__"
]

# comment: package-level version, sourced from pyproject.toml via get_logger_version()
__version__ = get_logger_version()
