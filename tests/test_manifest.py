import json
from pathlib import Path

from invoice_downloader.manifest import (
    InvoiceManifest,
    build_invoice_filename,
    compute_invoice_key,
    infer_invoice_date,
)


def test_compute_invoice_key_deduplicates_whitespace() -> None:
    first = compute_invoice_key("Invoice  Mar  2026", "https://example.com/a")
    second = compute_invoice_key("Invoice Mar 2026", "https://example.com/a")
    assert first == second


def test_build_invoice_filename_uses_date_and_key_prefix() -> None:
    filename = build_invoice_filename(
        "Invoice for 2026-03-22",
        "https://example.com/a",
        account_label="Yuichiro Iwamoto",
    )
    assert filename.startswith("yuichiro_iwamoto_invoice_2026-03-22_")
    assert filename.endswith(".pdf")


def test_infer_invoice_date_handles_unknown_value() -> None:
    assert infer_invoice_date("Invoice paid recently") == "unknown"


def test_manifest_roundtrip(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = InvoiceManifest.load(manifest_path)
    manifest.add(
        row_text="Invoice for 2026-03-01",
        href="https://example.com/invoice.pdf",
        filename="yuichiro_iwamoto_invoice_2026-03_deadbeef.pdf",
    )
    manifest.save()

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["invoices"]) == 1

    reloaded = InvoiceManifest.load(manifest_path)
    assert reloaded.has(compute_invoice_key("Invoice for 2026-03-01", "https://example.com/invoice.pdf"))
