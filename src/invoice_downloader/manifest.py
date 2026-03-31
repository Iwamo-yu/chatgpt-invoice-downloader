from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path


def normalize_invoice_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def compute_invoice_key(row_text: str, href: str | None = None) -> str:
    normalized = normalize_invoice_text(row_text).lower()
    href_part = (href or "").strip()
    digest = hashlib.sha1(f"{normalized}|{href_part}".encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()


def infer_invoice_date(row_text: str) -> str:
    normalized = normalize_invoice_text(row_text)
    iso_match = re.search(r"\b(20\d{2})[-/](0[1-9]|1[0-2])(?:[-/](0[1-9]|[12]\d|3[01]))?\b", normalized)
    if iso_match:
        if iso_match.group(3):
            return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"
        return f"{iso_match.group(1)}-{iso_match.group(2)}"

    month_match = re.search(
        r"\b("
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?"
        r")\s+(20\d{2})\b",
        normalized,
        re.IGNORECASE,
    )
    if month_match:
        months = {
            "jan": "01",
            "feb": "02",
            "mar": "03",
            "apr": "04",
            "may": "05",
            "jun": "06",
            "jul": "07",
            "aug": "08",
            "sep": "09",
            "oct": "10",
            "nov": "11",
            "dec": "12",
        }
        month_value = months[month_match.group(1)[:3].lower()]
        return f"{month_match.group(2)}-{month_value}"

    return "unknown"


def slugify_label(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower())
    return slug.strip("_") or "chatgpt"


def build_invoice_filename(
    row_text: str,
    href: str | None = None,
    *,
    account_label: str = "chatgpt",
) -> str:
    invoice_key = compute_invoice_key(row_text, href)
    invoice_date = infer_invoice_date(row_text)
    label = slugify_label(account_label)
    return f"{label}_invoice_{invoice_date}_{invoice_key[:8]}.pdf"


@dataclass(frozen=True)
class ManifestEntry:
    invoice_key: str
    row_text: str
    href: str | None
    filename: str
    downloaded_at: str
    drive_file_id: str | None = None
    drive_filename: str | None = None
    uploaded_to_drive_at: str | None = None


class InvoiceManifest:
    def __init__(self, path: Path, entries: dict[str, ManifestEntry] | None = None) -> None:
        self.path = path
        self.entries = entries or {}

    @classmethod
    def load(cls, path: Path) -> "InvoiceManifest":
        if not path.exists():
            return cls(path)

        payload = json.loads(path.read_text(encoding="utf-8"))
        invoices = payload.get("invoices", [])
        entries = {
            item["invoice_key"]: ManifestEntry(**item)
            for item in invoices
        }
        return cls(path, entries)

    def has(self, invoice_key: str) -> bool:
        return invoice_key in self.entries

    def add(
        self,
        *,
        row_text: str,
        href: str | None,
        filename: str,
        drive_file_id: str | None = None,
        drive_filename: str | None = None,
        uploaded_to_drive_at: str | None = None,
    ) -> ManifestEntry:
        entry = ManifestEntry(
            invoice_key=compute_invoice_key(row_text, href),
            row_text=normalize_invoice_text(row_text),
            href=href,
            filename=filename,
            downloaded_at=datetime.now(UTC).isoformat(),
            drive_file_id=drive_file_id,
            drive_filename=drive_filename,
            uploaded_to_drive_at=uploaded_to_drive_at,
        )
        self.entries[entry.invoice_key] = entry
        return entry

    def update(self, invoice_key: str, **changes: str | None) -> ManifestEntry:
        entry = self.entries[invoice_key]
        updated = replace(entry, **changes)
        self.entries[invoice_key] = updated
        return updated

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "invoices": [
                asdict(entry)
                for entry in sorted(self.entries.values(), key=lambda item: item.downloaded_at)
            ],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
