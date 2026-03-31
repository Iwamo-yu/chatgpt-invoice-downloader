from dataclasses import dataclass
from pathlib import Path

import pytest

from invoice_downloader.chatgpt import (
    BillingHistoryNotFoundError,
    InvoiceCandidate,
    InvoiceDownloadError,
    SessionExpiredError,
    SessionStateMissingError,
    run_download_flow,
    sync_saved_invoices,
)
from invoice_downloader.config import Settings
import invoice_downloader.chatgpt as chatgpt_module
from invoice_downloader.manifest import InvoiceManifest, build_invoice_filename


@dataclass
class FakePortal:
    candidates: list[InvoiceCandidate]
    session_error: Exception | None = None
    download_error: Exception | None = None
    downloads: list[tuple[InvoiceCandidate, Path]] | None = None
    debug_names: list[str] | None = None

    def __post_init__(self) -> None:
        if self.downloads is None:
            self.downloads = []
        if self.debug_names is None:
            self.debug_names = []

    def assert_session_valid(self) -> None:
        if self.session_error:
            raise self.session_error

    def open_billing_history(self) -> None:
        return None

    def list_invoice_candidates(self) -> list[InvoiceCandidate]:
        return self.candidates

    def download_invoice(self, candidate: InvoiceCandidate, destination: Path) -> None:
        if self.download_error:
            raise self.download_error
        self.downloads.append((candidate, destination))

    def save_debug_artifact(self, name: str) -> Path | None:
        self.debug_names.append(name)
        return None


def make_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        storage_state_path=(tmp_path / ".secrets/state.json"),
        download_dir=(tmp_path / "downloads"),
        invoice_manifest_path=(tmp_path / "downloads/manifest.json"),
        playwright_headless=True,
        playwright_timeout_ms=1000,
        playwright_browser_channel="chrome",
        chatgpt_access_token=None,
        account_label="Yuichiro Iwamoto",
        google_service_account_json=None,
        google_oauth_client_secret_json=None,
        google_oauth_token_json=(tmp_path / ".secrets/google-drive-token.json"),
        gdrive_folder_id=None,
    )
    settings.ensure_runtime_paths()
    settings.storage_state_path.write_text("{}", encoding="utf-8")
    return settings


def test_run_download_flow_requires_existing_session_state(tmp_path: Path) -> None:
    settings = Settings(
        storage_state_path=(tmp_path / ".secrets/state.json"),
        download_dir=(tmp_path / "downloads"),
        invoice_manifest_path=(tmp_path / "downloads/manifest.json"),
        playwright_headless=True,
        playwright_timeout_ms=1000,
        playwright_browser_channel="chrome",
        chatgpt_access_token=None,
        account_label="Yuichiro Iwamoto",
        google_service_account_json=None,
        google_oauth_client_secret_json=None,
        google_oauth_token_json=(tmp_path / ".secrets/google-drive-token.json"),
        gdrive_folder_id=None,
    )
    settings.ensure_runtime_paths()
    portal = FakePortal(candidates=[])

    with pytest.raises(SessionStateMissingError):
        run_download_flow(
            settings=settings,
            portal=portal,
            manifest=InvoiceManifest.load(settings.invoice_manifest_path),
        )


def test_run_download_flow_surfaces_session_expiry(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    portal = FakePortal(candidates=[], session_error=SessionExpiredError("expired"))

    with pytest.raises(SessionExpiredError, match="expired"):
        run_download_flow(
            settings=settings,
            portal=portal,
            manifest=InvoiceManifest.load(settings.invoice_manifest_path),
        )


def test_run_download_flow_fails_when_history_is_empty(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    portal = FakePortal(candidates=[])

    with pytest.raises(BillingHistoryNotFoundError):
        run_download_flow(
            settings=settings,
            portal=portal,
            manifest=InvoiceManifest.load(settings.invoice_manifest_path),
        )

    assert portal.debug_names == ["invoice-history-empty"]


def test_run_download_flow_downloads_pending_invoice_and_updates_manifest(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    candidate = InvoiceCandidate(row_text="Invoice for 2026-03-01", href="https://example.com/invoice.pdf")
    portal = FakePortal(candidates=[candidate])
    manifest = InvoiceManifest.load(settings.invoice_manifest_path)

    summary = run_download_flow(
        settings=settings,
        portal=portal,
        manifest=manifest,
    )

    assert summary.discovered_count == 1
    assert summary.pending_count == 1
    assert summary.downloaded_count == 1
    assert manifest.has(candidate.invoice_key)
    assert portal.downloads[0][1].name.startswith("yuichiro_iwamoto_invoice_2026-03-01_")


def test_run_download_flow_respects_dry_run(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    candidate = InvoiceCandidate(row_text="Invoice for 2026-03-01", href="https://example.com/invoice.pdf")
    portal = FakePortal(candidates=[candidate])
    manifest = InvoiceManifest.load(settings.invoice_manifest_path)

    summary = run_download_flow(
        settings=settings,
        portal=portal,
        manifest=manifest,
        dry_run=True,
        limit=1,
    )

    assert summary.pending_count == 1
    assert summary.downloaded_count == 0
    assert portal.downloads == []
    assert not manifest.has(candidate.invoice_key)


def test_run_download_flow_propagates_download_errors(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    candidate = InvoiceCandidate(row_text="Invoice for 2026-03-01", href="https://example.com/invoice.pdf")
    portal = FakePortal(candidates=[candidate], download_error=InvoiceDownloadError("boom"))

    with pytest.raises(InvoiceDownloadError, match="boom"):
        run_download_flow(
            settings=settings,
            portal=portal,
            manifest=InvoiceManifest.load(settings.invoice_manifest_path),
        )


def test_run_download_flow_uploads_to_drive_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        storage_state_path=settings.storage_state_path,
        download_dir=settings.download_dir,
        invoice_manifest_path=settings.invoice_manifest_path,
        playwright_headless=settings.playwright_headless,
        playwright_timeout_ms=settings.playwright_timeout_ms,
        playwright_browser_channel=settings.playwright_browser_channel,
        chatgpt_access_token=settings.chatgpt_access_token,
        account_label=settings.account_label,
        google_service_account_json=(tmp_path / "service_account.json"),
        google_oauth_client_secret_json=None,
        google_oauth_token_json=(tmp_path / ".secrets/google-drive-token.json"),
        gdrive_folder_id="folder123",
    )
    settings.google_service_account_json.write_text("{}", encoding="utf-8")
    candidate = InvoiceCandidate(row_text="Invoice for 2026-03-01", href="https://example.com/invoice.pdf")
    portal = FakePortal(candidates=[candidate])
    uploaded: dict[str, str] = {}

    monkeypatch.setattr(chatgpt_module, "drive_upload_enabled", lambda s: True)
    monkeypatch.setattr(
        chatgpt_module,
        "upload_to_drive",
        lambda settings, filepath, filename: uploaded.update({"filepath": str(filepath), "filename": filename}) or {"id": "drive123", "name": filename},
    )

    manifest = InvoiceManifest.load(settings.invoice_manifest_path)
    run_download_flow(
        settings=settings,
        portal=portal,
        manifest=manifest,
    )

    entry = manifest.entries[candidate.invoice_key]
    assert uploaded["filename"] == entry.filename
    assert entry.drive_file_id == "drive123"
    assert entry.drive_filename == entry.filename
    assert entry.uploaded_to_drive_at is not None


def test_sync_saved_invoices_renames_and_uploads_existing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(
        storage_state_path=settings.storage_state_path,
        download_dir=settings.download_dir,
        invoice_manifest_path=settings.invoice_manifest_path,
        playwright_headless=settings.playwright_headless,
        playwright_timeout_ms=settings.playwright_timeout_ms,
        playwright_browser_channel=settings.playwright_browser_channel,
        chatgpt_access_token=settings.chatgpt_access_token,
        account_label=settings.account_label,
        google_service_account_json=(tmp_path / "service_account.json"),
        google_oauth_client_secret_json=None,
        google_oauth_token_json=(tmp_path / ".secrets/google-drive-token.json"),
        gdrive_folder_id="folder123",
    )
    settings.google_service_account_json.write_text("{}", encoding="utf-8")

    manifest = InvoiceManifest.load(settings.invoice_manifest_path)
    entry = manifest.add(
        row_text="2026/03/22 $22.00 支払い済み ChatGPT Plus Subscription (per seat)",
        href="https://example.com/invoice.pdf",
        filename="chatgpt_invoice_2026-03_bc199ff0.pdf",
    )
    manifest.save()
    old_path = settings.download_dir / entry.filename
    old_path.write_bytes(b"pdf")
    uploaded: dict[str, str] = {}

    monkeypatch.setattr(chatgpt_module, "drive_upload_enabled", lambda s: True)
    monkeypatch.setattr(
        chatgpt_module,
        "upload_to_drive",
        lambda settings, filepath, filename: uploaded.update({"filepath": str(filepath), "filename": filename}) or {"id": "drive123", "name": filename},
    )

    summary = sync_saved_invoices(settings=settings, manifest=manifest)

    expected_name = build_invoice_filename(
        entry.row_text,
        entry.href,
        account_label=settings.account_label,
    )
    expected_path = settings.download_dir / expected_name
    assert summary.renamed_count == 1
    assert summary.uploaded_count == 1
    assert not old_path.exists()
    assert expected_path.exists()
    assert uploaded["filename"] == expected_name
    updated_entry = manifest.entries[entry.invoice_key]
    assert updated_entry.filename == expected_name
    assert updated_entry.drive_file_id == "drive123"
