"""Local browser CLI backend abstraction.

Defines the ``BrowserBackend`` ABC and two implementations:

- **AgentBrowserBackend** — wraps the ``agent-browser`` npm CLI (default)
- **BrowserUseBackend** — wraps the ``browser-use`` Python CLI

Both backends share the same subprocess execution model via ``run_cli()``.
The handler functions in ``browser_tool.py`` call backend methods like
``backend.click(ref, task_id)`` instead of building CLI commands directly.

Configuration: ``config["browser"]["cli_backend"]`` selects the backend.
Default is ``"agent-browser"`` for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BrowserBackend(ABC):
    """ABC for local browser CLI backends.

    Each implementation owns the full flow for its CLI: command building,
    element reference handling, and response parsing.  Shared subprocess
    execution lives in :meth:`run_cli` (set by ``browser_tool.py`` at
    startup since it depends on module-level state like session tracking).
    """

    cli_name: str = ""
    install_hint: str = ""
    supports_annotate: bool = False
    is_npm_based: bool = False

    def run_cli(
        self,
        task_id: str,
        command: str,
        args: Optional[List[str]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run a CLI command via the shared subprocess infrastructure.

        Delegates to ``_run_browser_command()`` in ``browser_tool.py``
        which handles session lookup, subprocess execution, JSON parsing, etc.
        Uses dynamic import to ensure mocks/patches work correctly in tests.
        """
        import tools.browser_tool as bt
        return bt._run_browser_command(task_id, command, args, timeout)

    @abstractmethod
    def find_cli(self) -> str:
        """Find the CLI executable.  Raise FileNotFoundError if not found."""

    def is_available(self) -> bool:
        """Return True if the CLI is findable on the system."""
        try:
            self.find_cli()
            return True
        except FileNotFoundError:
            return False

    # -- Browser actions (each returns normalized dict) ---------------------

    @abstractmethod
    def navigate(self, url: str, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def snapshot(self, full: bool, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def click(self, ref: str, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def type_text(self, ref: str, text: str, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def scroll(self, direction: str, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def back(self, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def press_key(self, key: str, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def close(self, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def screenshot(
        self, path: str, annotate: bool, full: bool, task_id: str,
    ) -> Dict[str, Any]:
        ...

    @abstractmethod
    def console(self, clear: bool, task_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def eval_js(self, code: str, task_id: str) -> Dict[str, Any]:
        ...

    # -- CDP flag (differs between CLIs) -----------------------------------

    def cdp_flag(self) -> str:
        """Return the CLI flag name for connecting via CDP (e.g. '--cdp')."""
        return "--cdp"


# ---------------------------------------------------------------------------
# AgentBrowserBackend
# ---------------------------------------------------------------------------

def _discover_homebrew_node_dirs() -> list[str]:
    """Find Homebrew versioned Node.js bin directories."""
    dirs: list[str] = []
    homebrew_opt = "/opt/homebrew/opt"
    if not os.path.isdir(homebrew_opt):
        return dirs
    try:
        for entry in os.listdir(homebrew_opt):
            if entry.startswith("node") and entry != "node":
                bin_dir = os.path.join(homebrew_opt, entry, "bin")
                if os.path.isdir(bin_dir):
                    dirs.append(bin_dir)
    except OSError:
        pass
    return dirs


class AgentBrowserBackend(BrowserBackend):
    """Backend wrapping the ``agent-browser`` npm CLI.

    This is a direct extraction of the existing behavior in browser_tool.py.
    """

    cli_name = "agent-browser"
    install_hint = (
        "agent-browser CLI not found. Install it with: npm install -g agent-browser\n"
        "Or run 'npm install' in the repo root to install locally.\n"
        "Or ensure npx is available in your PATH."
    )
    supports_annotate = True
    is_npm_based = True

    def find_cli(self) -> str:
        # System PATH
        which_result = shutil.which("agent-browser")
        if which_result:
            return which_result

        # Extended PATH (Homebrew, Hermes-managed node)
        extra_dirs: list[str] = []
        for d in ["/opt/homebrew/bin", "/usr/local/bin"]:
            if os.path.isdir(d):
                extra_dirs.append(d)
        extra_dirs.extend(_discover_homebrew_node_dirs())

        hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        hermes_node_bin = str(hermes_home / "node" / "bin")
        if os.path.isdir(hermes_node_bin):
            extra_dirs.append(hermes_node_bin)

        if extra_dirs:
            extended_path = os.pathsep.join(extra_dirs)
            which_result = shutil.which("agent-browser", path=extended_path)
            if which_result:
                return which_result

        # Local node_modules
        repo_root = Path(__file__).parent.parent.parent
        local_bin = repo_root / "node_modules" / ".bin" / "agent-browser"
        if local_bin.exists():
            return str(local_bin)

        # npx fallback
        npx_path = shutil.which("npx")
        if not npx_path and extra_dirs:
            npx_path = shutil.which("npx", path=os.pathsep.join(extra_dirs))
        if npx_path:
            return "npx agent-browser"

        raise FileNotFoundError(self.install_hint)

    def cdp_flag(self) -> str:
        return "--cdp"

    # -- Actions (current agent-browser behavior) --------------------------

    def navigate(self, url: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "open", [url], timeout=max(_get_command_timeout(), 60))

    def snapshot(self, full: bool, task_id: str) -> Dict[str, Any]:
        args = [] if full else ["-c"]
        return self.run_cli(task_id, "snapshot", args)

    def click(self, ref: str, task_id: str) -> Dict[str, Any]:
        if not ref.startswith("@"):
            ref = f"@{ref}"
        return self.run_cli(task_id, "click", [ref])

    def type_text(self, ref: str, text: str, task_id: str) -> Dict[str, Any]:
        if not ref.startswith("@"):
            ref = f"@{ref}"
        return self.run_cli(task_id, "fill", [ref, text])

    def scroll(self, direction: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "scroll", [direction])

    def back(self, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "back", [])

    def press_key(self, key: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "press", [key])

    def close(self, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "close", [])

    def screenshot(
        self, path: str, annotate: bool, full: bool, task_id: str,
    ) -> Dict[str, Any]:
        args: list[str] = []
        if annotate:
            args.append("--annotate")
        if full:
            args.append("--full")
        args.append(path)
        return self.run_cli(task_id, "screenshot", args)

    def console(self, clear: bool, task_id: str) -> Dict[str, Any]:
        # agent-browser has separate console + errors commands
        console_args = ["--clear"] if clear else []
        errors_args = ["--clear"] if clear else []
        console_result = self.run_cli(task_id, "console", console_args)
        errors_result = self.run_cli(task_id, "errors", errors_args)
        return _merge_console_results(console_result, errors_result)

    def eval_js(self, code: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "eval", [code])


# ---------------------------------------------------------------------------
# BrowserUseBackend
# ---------------------------------------------------------------------------

class BrowserUseBackend(BrowserBackend):
    """Backend wrapping the ``browser-use`` Python CLI."""

    cli_name = "browser-use"
    install_hint = (
        "browser-use CLI not found. Install it with:\n"
        "  curl -fsSL https://browser-use.com/cli/install_lite.sh | bash"
    )
    supports_annotate = False
    is_npm_based = False

    def find_cli(self) -> str:
        which_result = shutil.which("browser-use")
        if which_result:
            return which_result
        # Also check common aliases
        for alias in ("bu", "browseruse"):
            which_result = shutil.which(alias)
            if which_result:
                return which_result
        raise FileNotFoundError(self.install_hint)

    def cdp_flag(self) -> str:
        return "--cdp-url"

    # -- Actions (browser-use native commands) -----------------------------

    def navigate(self, url: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "open", [url], timeout=max(_get_command_timeout(), 60))

    def snapshot(self, full: bool, task_id: str) -> Dict[str, Any]:
        # browser-use uses "state" instead of "snapshot", no compact flag
        result = self.run_cli(task_id, "state", [])
        return _normalize_browser_use_snapshot(result)

    def click(self, ref: str, task_id: str) -> Dict[str, Any]:
        ref = _strip_ref_prefix(ref)
        return self.run_cli(task_id, "click", [ref])

    def type_text(self, ref: str, text: str, task_id: str) -> Dict[str, Any]:
        ref = _strip_ref_prefix(ref)
        return self.run_cli(task_id, "input", [ref, text])

    def scroll(self, direction: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "scroll", [direction])

    def back(self, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "back", [])

    def press_key(self, key: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "keys", [key])

    def close(self, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "close", [])

    def screenshot(
        self, path: str, annotate: bool, full: bool, task_id: str,
    ) -> Dict[str, Any]:
        args: list[str] = []
        # browser-use doesn't support --annotate
        if full:
            args.append("--full")
        args.append(path)
        return self.run_cli(task_id, "screenshot", args)

    def console(self, clear: bool, task_id: str) -> Dict[str, Any]:
        # browser-use doesn't have console/errors commands — use eval workaround
        # Inject interceptor on first call, read buffer on subsequent calls
        interceptor_js = (
            "(() => {"
            "  if (!window.__hermes_console) {"
            "    window.__hermes_console = [];"
            "    window.__hermes_errors = [];"
            "    const orig = console.log.bind(console);"
            "    ['log','warn','error','info'].forEach(m => {"
            "      const o = console[m].bind(console);"
            "      console[m] = (...a) => {"
            "        window.__hermes_console.push({type:m, text:a.map(String).join(' '), ts:Date.now()});"
            "        o(...a);"
            "      };"
            "    });"
            "    window.addEventListener('error', e => {"
            "      window.__hermes_errors.push({message:e.message, ts:Date.now()});"
            "    });"
            "  }"
            "  const msgs = [...window.__hermes_console];"
            "  const errs = [...window.__hermes_errors];"
            f"  {'window.__hermes_console=[];window.__hermes_errors=[];' if clear else ''}"
            "  return JSON.stringify({messages:msgs, errors:errs});"
            "})()"
        )
        result = self.run_cli(task_id, "eval", [interceptor_js])
        return _normalize_browser_use_console(result)

    def eval_js(self, code: str, task_id: str) -> Dict[str, Any]:
        return self.run_cli(task_id, "eval", [code])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_command_timeout() -> int:
    """Read browser.command_timeout from config (shared with browser_tool.py)."""
    try:
        hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        config_path = hermes_home / "config.yaml"
        if config_path.exists():
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            val = cfg.get("browser", {}).get("command_timeout")
            if val is not None:
                return max(int(val), 5)
    except Exception:
        pass
    return 30


def _strip_ref_prefix(ref: str) -> str:
    """Strip agent-browser-style ref prefix for browser-use.

    Handles: ``@e5`` → ``5``, ``@5`` → ``5``, ``e5`` → ``5``, ``5`` → ``5``
    """
    ref = ref.strip()
    if ref.startswith("@"):
        ref = ref[1:]
    if ref.startswith("e") and ref[1:].isdigit():
        ref = ref[1:]
    return ref


def _normalize_browser_use_snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize browser-use ``state`` response to match expected snapshot format."""
    if not result.get("success"):
        return result
    data = result.get("data", {})
    raw_text = data.get("_raw_text", "")
    return {
        "success": True,
        "data": {
            "snapshot": raw_text,
            "refs": {},
        },
    }


def _normalize_browser_use_console(result: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the eval result from the console interceptor."""
    if not result.get("success"):
        return result
    data = result.get("data", {})
    raw = data.get("result", "{}")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        parsed = {"messages": [], "errors": []}

    console_messages = [
        {"type": m.get("type", "log"), "text": m.get("text", ""), "source": "console"}
        for m in parsed.get("messages", [])
    ]
    js_errors = [
        {"message": e.get("message", ""), "source": "exception"}
        for e in parsed.get("errors", [])
    ]
    return {
        "success": True,
        "data": {
            "messages": console_messages,
            "errors": js_errors,
        },
    }


def _merge_console_results(
    console_result: Dict[str, Any],
    errors_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge agent-browser's separate console + errors responses."""
    messages = []
    if console_result.get("success"):
        for msg in console_result.get("data", {}).get("messages", []):
            msg["source"] = "console"
            messages.append(msg)

    errors = []
    if errors_result.get("success"):
        for err in errors_result.get("data", {}).get("errors", []):
            err["source"] = "exception"
            errors.append(err)

    return {
        "success": True,
        "data": {
            "messages": messages,
            "errors": errors,
        },
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CLI_BACKEND_REGISTRY: Dict[str, type] = {
    "agent-browser": AgentBrowserBackend,
    "browser-use": BrowserUseBackend,
}
