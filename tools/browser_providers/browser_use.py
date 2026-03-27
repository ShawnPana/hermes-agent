"""Browser Use cloud browser provider."""

import logging
import os
import uuid
from typing import Dict

import requests

from tools.browser_providers.base import CloudBrowserProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.browser-use.com/api/v2"


class BrowserUseProvider(CloudBrowserProvider):
    """Browser Use (https://browser-use.com) cloud browser backend."""

    def provider_name(self) -> str:
        return "Browser Use"

    def is_configured(self) -> bool:
        return bool(os.environ.get("BROWSER_USE_API_KEY"))

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        api_key = os.environ.get("BROWSER_USE_API_KEY")
        if not api_key:
            raise ValueError(
                "BROWSER_USE_API_KEY environment variable is required. "
                "Get your key at https://browser-use.com"
            )
        return {
            "Content-Type": "application/json",
            "X-Browser-Use-API-Key": api_key,
        }

    def _get_session_config(self) -> dict:
        """Read session config from ``browser.providers.browser-use`` in config.yaml.

        Any keys found are passed straight through to the Browser Use API
        when creating a session.  This allows users to set ``profile_id``,
        ``proxy_country``, ``timeout``, or any other API-supported field
        without code changes.
        """
        try:
            from pathlib import Path
            import yaml
            hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
            config_path = hermes_home / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                return dict(cfg.get("browser", {}).get("providers", {}).get("browser-use", {}) or {})
        except Exception:
            pass
        return {}

    def create_session(self, task_id: str) -> Dict[str, object]:
        session_config = self._get_session_config()
        response = requests.post(
            f"{_BASE_URL}/browsers",
            headers=self._headers(),
            json=session_config,
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"Failed to create Browser Use session: "
                f"{response.status_code} {response.text}"
            )

        session_data = response.json()
        session_name = f"hermes_{task_id}_{uuid.uuid4().hex[:8]}"

        logger.info("Created Browser Use session %s", session_name)

        return {
            "session_name": session_name,
            "bb_session_id": session_data["id"],
            "cdp_url": session_data["cdpUrl"],
            "features": {"browser_use": True},
        }

    def close_session(self, session_id: str) -> bool:
        try:
            response = requests.patch(
                f"{_BASE_URL}/browsers/{session_id}",
                headers=self._headers(),
                json={"action": "stop"},
                timeout=10,
            )
            if response.status_code in (200, 201, 204):
                logger.debug("Successfully closed Browser Use session %s", session_id)
                return True
            else:
                logger.warning(
                    "Failed to close Browser Use session %s: HTTP %s - %s",
                    session_id,
                    response.status_code,
                    response.text[:200],
                )
                return False
        except Exception as e:
            logger.error("Exception closing Browser Use session %s: %s", session_id, e)
            return False

    def emergency_cleanup(self, session_id: str) -> None:
        api_key = os.environ.get("BROWSER_USE_API_KEY")
        if not api_key:
            logger.warning("Cannot emergency-cleanup Browser Use session %s — missing credentials", session_id)
            return
        try:
            requests.patch(
                f"{_BASE_URL}/browsers/{session_id}",
                headers={
                    "Content-Type": "application/json",
                    "X-Browser-Use-API-Key": api_key,
                },
                json={"action": "stop"},
                timeout=5,
            )
        except Exception as e:
            logger.debug("Emergency cleanup failed for Browser Use session %s: %s", session_id, e)
