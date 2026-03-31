from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


def _parse_bool(raw: str | bool | None, *, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean value: {raw!r}")


@dataclass(frozen=True)
class Settings:
    storage_state_path: Path
    download_dir: Path
    invoice_manifest_path: Path
    playwright_headless: bool
    playwright_timeout_ms: int
    playwright_browser_channel: str | None
    chatgpt_access_token: str | None
    account_label: str
    google_service_account_json: Path | None
    google_oauth_client_secret_json: Path | None
    google_oauth_token_json: Path | None
    gdrive_folder_id: str | None

    def ensure_runtime_paths(self) -> None:
        self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.invoice_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if self.google_oauth_token_json is not None:
            self.google_oauth_token_json.parent.mkdir(parents=True, exist_ok=True)


def load_settings(
    *,
    env_file: str | os.PathLike[str] = ".env",
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Settings:
    root = cwd or Path.cwd()
    merged: dict[str, str | None] = {}
    env_path = root / Path(env_file)
    if env_path.exists():
        merged.update(dotenv_values(env_path))
    merged.update(dict(environ or os.environ))

    storage_state_raw = merged.get("CHATGPT_STORAGE_STATE_PATH")
    download_dir_raw = merged.get("DOWNLOAD_DIR")
    manifest_raw = merged.get("INVOICE_MANIFEST_PATH")

    missing = [
        name
        for name, value in {
            "CHATGPT_STORAGE_STATE_PATH": storage_state_raw,
            "DOWNLOAD_DIR": download_dir_raw,
            "INVOICE_MANIFEST_PATH": manifest_raw,
        }.items()
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise ConfigurationError(f"Missing required configuration: {joined}")

    timeout_raw = merged.get("PLAYWRIGHT_TIMEOUT_MS", "15000")
    try:
        timeout_ms = int(str(timeout_raw))
    except ValueError as exc:
        raise ConfigurationError(
            f"PLAYWRIGHT_TIMEOUT_MS must be an integer, got {timeout_raw!r}"
        ) from exc
    if timeout_ms <= 0:
        raise ConfigurationError("PLAYWRIGHT_TIMEOUT_MS must be greater than zero")

    settings = Settings(
        storage_state_path=(root / str(storage_state_raw)).resolve(),
        download_dir=(root / str(download_dir_raw)).resolve(),
        invoice_manifest_path=(root / str(manifest_raw)).resolve(),
        playwright_headless=_parse_bool(
            merged.get("PLAYWRIGHT_HEADLESS"), default=True
        ),
        playwright_timeout_ms=timeout_ms,
        playwright_browser_channel=(
            str(merged.get("PLAYWRIGHT_BROWSER_CHANNEL", "chrome")).strip() or None
        ),
        chatgpt_access_token=(
            str(merged.get("CHATGPT_ACCESS_TOKEN")).strip()
            if merged.get("CHATGPT_ACCESS_TOKEN")
            else None
        ),
        account_label=str(merged.get("ACCOUNT_LABEL", "chatgpt")).strip() or "chatgpt",
        google_service_account_json=(
            (root / str(merged.get("GOOGLE_SERVICE_ACCOUNT_JSON"))).resolve()
            if merged.get("GOOGLE_SERVICE_ACCOUNT_JSON")
            else None
        ),
        google_oauth_client_secret_json=(
            (root / str(merged.get("GOOGLE_OAUTH_CLIENT_SECRET_JSON"))).resolve()
            if merged.get("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
            else None
        ),
        google_oauth_token_json=(
            (root / str(merged.get("GOOGLE_OAUTH_TOKEN_JSON"))).resolve()
            if merged.get("GOOGLE_OAUTH_TOKEN_JSON")
            else (root / ".secrets/google-drive-token.json").resolve()
        ),
        gdrive_folder_id=(
            str(merged.get("GDRIVE_FOLDER_ID")).strip()
            if merged.get("GDRIVE_FOLDER_ID")
            else None
        ),
    )
    settings.ensure_runtime_paths()
    return settings
