"""Browser provider abstractions.

Import the ABCs so callers can do::

    from tools.browser_providers import CloudBrowserProvider, BrowserBackend
"""

from tools.browser_providers.base import CloudBrowserProvider
from tools.browser_providers.local_cli import BrowserBackend

__all__ = ["CloudBrowserProvider", "BrowserBackend"]
