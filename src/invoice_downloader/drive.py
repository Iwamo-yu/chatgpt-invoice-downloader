from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import Settings

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def drive_upload_enabled(settings: Settings) -> bool:
    return bool(
        settings.gdrive_folder_id
        and (
            settings.google_service_account_json
            or settings.google_oauth_client_secret_json
        )
    )


def _oauth_credentials(settings: Settings) -> UserCredentials:
    if not settings.google_oauth_client_secret_json:
        raise ValueError(
            "Google Drive OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_SECRET_JSON."
        )

    creds: UserCredentials | None = None
    if settings.google_oauth_token_json and settings.google_oauth_token_json.exists():
        creds = UserCredentials.from_authorized_user_file(
            str(settings.google_oauth_token_json),
            DRIVE_SCOPES,
        )

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        settings.google_oauth_token_json.write_text(creds.to_json(), encoding="utf-8")
        return creds

    raise ValueError(
        "Google Drive OAuth token is missing or invalid. "
        "Run `uv run invoice-downloader prepare-drive-auth`."
    )


def prepare_drive_auth(settings: Settings) -> Path:
    if not settings.google_oauth_client_secret_json:
        raise ValueError(
            "GOOGLE_OAUTH_CLIENT_SECRET_JSON is required for OAuth setup."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.google_oauth_client_secret_json),
        DRIVE_SCOPES,
    )
    creds = flow.run_local_server(port=0)
    if settings.google_oauth_token_json is None:
        raise ValueError("GOOGLE_OAUTH_TOKEN_JSON is not configured.")
    settings.google_oauth_token_json.write_text(creds.to_json(), encoding="utf-8")
    return settings.google_oauth_token_json


def _build_drive_service(settings: Settings):
    if settings.google_service_account_json:
        creds = Credentials.from_service_account_file(
            str(settings.google_service_account_json),
            scopes=["https://www.googleapis.com/auth/drive"],
        )
    else:
        creds = _oauth_credentials(settings)
    return build("drive", "v3", credentials=creds)


def upload_to_drive(settings: Settings, filepath: Path, filename: str) -> dict[str, str]:
    if not settings.gdrive_folder_id:
        raise ValueError("Google Drive upload is not configured.")

    service = _build_drive_service(settings)
    metadata = {
        "name": filename,
        "parents": [settings.gdrive_folder_id],
    }
    media = MediaFileUpload(str(filepath), mimetype="application/pdf")
    created = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return {"id": created["id"], "name": created["name"]}
