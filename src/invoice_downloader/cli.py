from __future__ import annotations

import argparse
import sys

from .chatgpt import (
    BillingHistoryNotFoundError,
    InvoiceDownloadError,
    SessionExpiredError,
    SessionStateMissingError,
    download_invoices,
    prepare_session,
    sync_saved_invoices,
)
from .config import ConfigurationError, load_settings
from .drive import prepare_drive_auth
from .manifest import InvoiceManifest


EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 1
EXIT_SESSION_MISSING = 2
EXIT_SESSION_EXPIRED = 3
EXIT_BILLING_HISTORY_NOT_FOUND = 4
EXIT_DOWNLOAD_FAILED = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-downloader",
        description="Download ChatGPT web subscription invoices using a saved browser session.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the environment file to load. Useful for multi-account runs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare-session", help="Open a browser and save an authenticated ChatGPT session."
    )
    prepare_parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Chromium with a visible window. Recommended for manual login.",
    )

    subparsers.add_parser(
        "prepare-drive-auth",
        help="Open a browser and save a Google Drive OAuth token for My Drive uploads.",
    )

    download_parser = subparsers.add_parser(
        "download", help="Download ChatGPT invoices using the saved session."
    )
    download_parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Chromium with a visible window.",
    )
    download_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process up to N pending invoices.",
    )
    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List reachable invoices without downloading them.",
    )

    subparsers.add_parser(
        "sync-storage",
        help="Rename existing downloaded invoices and upload any missing Google Drive copies.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings(env_file=args.env_file)
        if args.command == "prepare-session":
            path = prepare_session(settings, headed=True if args.headed or not settings.playwright_headless else False)
            print(f"Saved session to {path}")
            return EXIT_OK
        if args.command == "prepare-drive-auth":
            path = prepare_drive_auth(settings)
            print(f"Saved Google Drive OAuth token to {path}")
            return EXIT_OK
        if args.command == "sync-storage":
            manifest = InvoiceManifest.load(settings.invoice_manifest_path)
            summary = sync_saved_invoices(settings=settings, manifest=manifest)
            print(
                "Storage sync complete: "
                f"renamed={summary.renamed_count} "
                f"uploaded={summary.uploaded_count}"
            )
            return EXIT_OK

        summary = download_invoices(
            settings,
            headed=True if args.headed else None,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        print(
            "Invoice run complete: "
            f"discovered={summary.discovered_count} "
            f"pending={summary.pending_count} "
            f"downloaded={summary.downloaded_count} "
            f"dry_run={summary.dry_run}"
        )
        return EXIT_OK
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIGURATION_ERROR
    except SessionStateMissingError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_SESSION_MISSING
    except SessionExpiredError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_SESSION_EXPIRED
    except BillingHistoryNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_BILLING_HISTORY_NOT_FOUND
    except InvoiceDownloadError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_DOWNLOAD_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
