"""
Syncthing REST API client for hermes-sidecar.

Provides a SyncthingClient class wrapping the Syncthing REST API (localhost:8384/rest).
Auto-detects the API key from Syncthing's config.xml on instantiation.
"""

import json
import os
import platform
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
IS_MACOS = platform.system() == "Darwin"

if IS_MACOS:
    _SYNCTHING_CONFIG_XML = (
        HOME / "Library" / "Application Support" / "Syncthing" / "config.xml"
    )
else:
    _SYNCTHING_CONFIG_XML = HOME / ".config" / "syncthing" / "config.xml"

DEFAULT_API_BASE = "http://localhost:8384"


class SyncthingClient:
    """Client for the Syncthing REST API.

    Auto-detects the API key from Syncthing's config.xml. Falls back to the
    SYNCTHING_API_KEY environment variable if the config file is unavailable.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: str = DEFAULT_API_BASE,
    ) -> None:
        """Initialize the client.

        Args:
            api_key: Pre-configured API key. If None, auto-detected from config.xml
                     or SYNCTHING_API_KEY env var.
            api_base: Base URL for the Syncthing REST API.
        """
        self.api_base: str = api_base.rstrip("/")
        self.api_key: Optional[str] = api_key or self._detect_api_key()

    # ── API key detection ──────────────────────────────────────────

    @staticmethod
    def _detect_api_key() -> Optional[str]:
        """Detect the Syncthing API key from config.xml or environment."""
        # Try config.xml first
        if _SYNCTHING_CONFIG_XML.exists():
            try:
                content = _SYNCTHING_CONFIG_XML.read_text()
                m = re.search(r"<apikey>([^<]+)</apikey>", content)
                if m:
                    return m.group(1)
            except Exception:
                pass

        # Fall back to environment variable
        return os.environ.get("SYNCTHING_API_KEY")

    # ── low-level HTTP helpers ─────────────────────────────────────

    def _get(self, path: str, timeout: int = 5) -> Dict[str, Any]:
        """GET from the Syncthing REST API.

        Args:
            path: API path relative to the base URL (e.g. '/rest/system/status').
            timeout: Request timeout in seconds.

        Returns:
            Parsed JSON response dict, or {'error': str} on failure.
        """
        if not self.api_key:
            return {"error": "No API key configured"}
        url = f"{self.api_base}{path}"
        req = urllib.request.Request(url)
        req.add_header("X-API-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}

    def _patch(self, path: str, data: Dict[str, Any], timeout: int = 10) -> bool:
        """PATCH the Syncthing REST API (mutating calls require CSRF token).

        Args:
            path: API path relative to the base URL.
            data: JSON-serializable dict to send as the PATCH body.
            timeout: Request timeout in seconds.

        Returns:
            True if the request succeeded (2xx status), False otherwise.
        """
        if not self.api_key:
            return False

        # Fetch a fresh CSRF token from /rest/system/status
        csrf_token = "0"
        status = self._get("/rest/system/status")
        if "error" not in status:
            try:
                csrf_token = str(status.get("options", {}).get("urAccepted", "0"))
            except Exception:
                pass

        url = f"{self.api_base}{path}"
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="PATCH")
        req.add_header("X-API-Key", self.api_key)
        req.add_header(f"X-CSRF-Token-{csrf_token}", "hermes-sidecar")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    # ── public API ──────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Retrieve Syncthing system status.

        Returns:
            Dict with keys like uptime, cpuPercent, alloc, options, etc.
            Contains 'error' key on failure.
        """
        return self._get("/rest/system/status")

    def get_folder_stats(self) -> Dict[str, Any]:
        """Retrieve completion statistics for all folders.

        Returns:
            Dict mapping folder IDs to {completion, needBytes, ...}.
        """
        return self._get("/rest/db/completion")

    def get_config(self) -> Dict[str, Any]:
        """Retrieve the full Syncthing configuration (GET /rest/config).

        Returns:
            Full config dict with devices, folders, options, etc.
        """
        return self._get("/rest/config")

    def set_options(self, options: Dict[str, Any]) -> bool:
        """Set global Syncthing options via PATCH /rest/config/options.

        Args:
            options: Dict of option keys to set (e.g. {'maxSendKbps': 500}).

        Returns:
            True on success.
        """
        return self._patch("/rest/config/options", options)

    def set_throttle(self, throttle_kbps: int) -> bool:
        """Set bandwidth throttle for send and receive.

        Args:
            throttle_kbps: Bandwidth limit in KB/s. 0 means unlimited.

        Returns:
            True on success.
        """
        return self.set_options({
            "maxSendKbps": throttle_kbps,
            "maxRecvKbps": throttle_kbps,
        })

    def pause_device(self, device_id: str) -> bool:
        """Pause a specific device.

        Args:
            device_id: Syncthing device ID (long hash string).

        Returns:
            True on success.
        """
        return self._patch(f"/rest/config/devices/{device_id}", {"paused": True})

    def resume_device(self, device_id: str) -> bool:
        """Resume a specific device.

        Args:
            device_id: Syncthing device ID (long hash string).

        Returns:
            True on success.
        """
        return self._patch(f"/rest/config/devices/{device_id}", {"paused": False})

    def get_connections(self) -> Dict[str, Any]:
        """Retrieve active device connections.

        Returns:
            Dict with 'connections' key mapping device IDs to connection info.
        """
        return self._get("/rest/system/connections")

    def get_folders(self) -> List[Dict[str, Any]]:
        """Retrieve the list of configured folders.

        Returns:
            List of folder config dicts, each with id, path, paused, etc.
        """
        result = self._get("/rest/config/folders")
        if isinstance(result, list):
            return result
        return []

    def set_folder_paused(self, folder_id: str, paused: bool = True) -> bool:
        """Pause or resume a specific folder.

        Args:
            folder_id: The Syncthing folder ID.
            paused: True to pause, False to resume.

        Returns:
            True on success.
        """
        return self._patch(f"/rest/config/folders/{folder_id}", {"paused": paused})

    def pause_all_folders(self) -> bool:
        """Pause every configured folder.

        Returns:
            True if all patches succeeded.
        """
        folders = self.get_folders()
        if not folders:
            return False
        return all(
            self.set_folder_paused(f["id"], True)
            for f in folders
        )

    def resume_all_folders(self) -> bool:
        """Resume every configured folder.

        Returns:
            True if all patches succeeded.
        """
        folders = self.get_folders()
        if not folders:
            return False
        return all(
            self.set_folder_paused(f["id"], False)
            for f in folders
        )
