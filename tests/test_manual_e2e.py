from __future__ import annotations

import os

import pytest

from invoice_downloader.chatgpt import download_invoices, prepare_session
from invoice_downloader.config import load_settings


pytestmark = pytest.mark.manual_e2e


@pytest.mark.skipif(
    os.getenv("RUN_MANUAL_E2E") != "1",
    reason="Set RUN_MANUAL_E2E=1 to run the manual browser-based invoice flow.",
)
def test_manual_chatgpt_invoice_flow() -> None:
    settings = load_settings()

    if not settings.storage_state_path.exists():
        saved_path = prepare_session(settings, headed=True)
        assert saved_path.exists()

    dry_run_summary = download_invoices(
        settings,
        headed=True,
        dry_run=True,
        limit=1,
    )
    assert dry_run_summary.discovered_count >= 1
    assert dry_run_summary.pending_count >= 0

    download_summary = download_invoices(
        settings,
        headed=True,
        dry_run=False,
        limit=1,
    )
    assert download_summary.downloaded_count >= 1
    assert settings.invoice_manifest_path.exists()
