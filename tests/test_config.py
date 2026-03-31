from pathlib import Path

import pytest

from invoice_downloader.config import ConfigurationError, load_settings


def test_load_settings_reads_required_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CHATGPT_STORAGE_STATE_PATH=.secrets/state.json",
                "DOWNLOAD_DIR=downloads",
                "INVOICE_MANIFEST_PATH=downloads/manifest.json",
                "PLAYWRIGHT_HEADLESS=false",
                "PLAYWRIGHT_TIMEOUT_MS=20000",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file.name, environ={}, cwd=tmp_path)

    assert settings.storage_state_path == (tmp_path / ".secrets/state.json").resolve()
    assert settings.download_dir == (tmp_path / "downloads").resolve()
    assert settings.invoice_manifest_path == (tmp_path / "downloads/manifest.json").resolve()
    assert settings.playwright_headless is False
    assert settings.playwright_timeout_ms == 20000
    assert settings.playwright_browser_channel == "chrome"
    assert settings.chatgpt_access_token is None
    assert settings.account_label == "chatgpt"
    assert settings.google_service_account_json is None
    assert settings.google_oauth_client_secret_json is None
    assert settings.google_oauth_token_json == (tmp_path / ".secrets/google-drive-token.json").resolve()
    assert settings.gdrive_folder_id is None
    assert settings.download_dir.exists()


def test_load_settings_reads_optional_drive_configuration(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CHATGPT_STORAGE_STATE_PATH=.secrets/state.json",
                "DOWNLOAD_DIR=downloads",
                "INVOICE_MANIFEST_PATH=downloads/manifest.json",
                "GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json",
                "GDRIVE_FOLDER_ID=folder123",
                "ACCOUNT_LABEL=Yuichiro Iwamoto",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file.name, environ={}, cwd=tmp_path)

    assert settings.account_label == "Yuichiro Iwamoto"
    assert settings.google_service_account_json == (tmp_path / "service_account.json").resolve()
    assert settings.gdrive_folder_id == "folder123"


def test_load_settings_reads_optional_oauth_drive_configuration(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CHATGPT_STORAGE_STATE_PATH=.secrets/state.json",
                "DOWNLOAD_DIR=downloads",
                "INVOICE_MANIFEST_PATH=downloads/manifest.json",
                "GOOGLE_OAUTH_CLIENT_SECRET_JSON=oauth_client_secret.json",
                "GOOGLE_OAUTH_TOKEN_JSON=.secrets/drive-token.json",
                "GDRIVE_FOLDER_ID=folder123",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file.name, environ={}, cwd=tmp_path)

    assert settings.google_oauth_client_secret_json == (tmp_path / "oauth_client_secret.json").resolve()
    assert settings.google_oauth_token_json == (tmp_path / ".secrets/drive-token.json").resolve()
    assert settings.gdrive_folder_id == "folder123"


def test_load_settings_requires_mandatory_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="CHATGPT_STORAGE_STATE_PATH"):
        load_settings(
            environ={
                "DOWNLOAD_DIR": "downloads",
                "INVOICE_MANIFEST_PATH": "downloads/manifest.json",
            },
            cwd=tmp_path,
        )


def test_load_settings_rejects_invalid_boolean(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Invalid boolean value"):
        load_settings(
            environ={
                "CHATGPT_STORAGE_STATE_PATH": ".secrets/state.json",
                "DOWNLOAD_DIR": "downloads",
                "INVOICE_MANIFEST_PATH": "downloads/manifest.json",
                "PLAYWRIGHT_HEADLESS": "sometimes",
            },
            cwd=tmp_path,
        )
