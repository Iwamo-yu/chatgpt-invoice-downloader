from __future__ import annotations

import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from datetime import UTC, datetime

from playwright.sync_api import Browser, BrowserContext, Error, Frame, Page, TimeoutError, sync_playwright

from .config import Settings
from .drive import drive_upload_enabled, upload_to_drive
from .manifest import InvoiceManifest, build_invoice_filename, compute_invoice_key, infer_invoice_date


class InvoiceDownloaderError(RuntimeError):
    """Base exception for invoice downloader failures."""


class SessionStateMissingError(InvoiceDownloaderError):
    """Raised when the saved browser session is missing."""


class SessionExpiredError(InvoiceDownloaderError):
    """Raised when the saved session is no longer authenticated."""


class BillingHistoryNotFoundError(InvoiceDownloaderError):
    """Raised when invoice history cannot be found or is empty."""


class InvoiceDownloadError(InvoiceDownloaderError):
    """Raised when invoice downloading fails."""


@dataclass(frozen=True)
class InvoiceCandidate:
    row_text: str
    href: str | None = None

    @property
    def invoice_key(self) -> str:
        return compute_invoice_key(self.row_text, self.href)

    @property
    def invoice_date(self) -> str:
        return infer_invoice_date(self.row_text)

    @property
    def filename(self) -> str:
        return build_invoice_filename(self.row_text, self.href)


@dataclass(frozen=True)
class DownloadSummary:
    discovered_count: int
    pending_count: int
    downloaded_count: int
    dry_run: bool


@dataclass(frozen=True)
class StorageSyncSummary:
    renamed_count: int
    uploaded_count: int


class ChatGPTPortal(Protocol):
    def assert_session_valid(self) -> None: ...

    def open_billing_history(self) -> None: ...

    def list_invoice_candidates(self) -> list[InvoiceCandidate]: ...

    def download_invoice(self, candidate: InvoiceCandidate, destination: Path) -> None: ...

    def save_debug_artifact(self, name: str) -> Path | None: ...


@dataclass(frozen=True)
class PortalRuntime:
    browser: Browser
    context: BrowserContext
    page: Page


def _wait_for_chatgpt_shell(page: Page, *, timeout: int) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout)
    page.wait_for_timeout(1500)


def _wait_for_stripe_invoice_page(page: Page, *, timeout: int) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout)
    try:
        page.locator(".FullPageMessage").wait_for(state="hidden", timeout=timeout)
    except Exception:
        page.wait_for_timeout(3000)
    page.wait_for_timeout(1000)


def _playwright_launch_kwargs(settings: Settings, *, headed: bool | None) -> dict[str, object]:
    is_headed = False if headed is None else headed
    return {
        "headless": settings.playwright_headless if headed is None else not headed,
        "channel": settings.playwright_browser_channel,
        "args": (
            ["--disable-blink-features=AutomationControlled"]
            if is_headed
            else []
        ),
    }


class PlaywrightChatGPTPortal(AbstractContextManager["PlaywrightChatGPTPortal"]):
    def __init__(self, settings: Settings, *, headed: bool | None = None) -> None:
        self.settings = settings
        self.headed = headed
        self._playwright = None
        self._runtime: PortalRuntime | None = None

    def __enter__(self) -> "PlaywrightChatGPTPortal":
        self.settings.ensure_runtime_paths()
        playwright_cm = sync_playwright()
        playwright = playwright_cm.__enter__()
        browser = playwright.chromium.launch(
            **_playwright_launch_kwargs(self.settings, headed=self.headed)
        )
        context = browser.new_context(
            storage_state=str(self.settings.storage_state_path),
            accept_downloads=True,
        )
        page = context.new_page()
        self._playwright = playwright_cm
        self._runtime = PortalRuntime(browser=browser, context=context, page=page)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._runtime is not None:
            self._runtime.context.close()
            self._runtime.browser.close()
            self._runtime = None
        if self._playwright is not None:
            self._playwright.__exit__(exc_type, exc, tb)
            self._playwright = None
        return None

    @property
    def page(self) -> Page:
        if self._runtime is None:
            raise RuntimeError("Portal is not active")
        return self._runtime.page

    def save_debug_artifact(self, name: str) -> Path | None:
        debug_dir = self.settings.download_dir.parent / "playwright-debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{name}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
        except Exception:
            return None
        return path

    def _billing_target(self) -> Page | Frame:
        page = self.page
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            frame_url = frame.url or ""
            if "billing.stripe.com" in frame_url or "customer-portal" in frame_url:
                return frame

        iframe_locator = page.locator("iframe[src*='billing.stripe.com'], iframe[title*='Customer portal']")
        if iframe_locator.count():
            page.wait_for_timeout(1000)
            for frame in page.frames:
                frame_url = frame.url or ""
                if "billing.stripe.com" in frame_url or "customer-portal" in frame_url:
                    return frame

        return page

    def _resolve_billing_page(self) -> Page | None:
        if self._runtime is None:
            return None

        for candidate in reversed(self._runtime.context.pages):
            candidate_url = candidate.url or ""
            if (
                "billing.stripe.com" in candidate_url
                or "invoice.stripe.com" in candidate_url
                or "customer-portal" in candidate_url
            ):
                return candidate

        current_page = self.page
        current_url = current_page.url or ""
        if (
            "billing.stripe.com" in current_url
            or "invoice.stripe.com" in current_url
            or "customer-portal" in current_url
        ):
            return current_page

        return None

    def _prompt_for_manual_billing_navigation(self) -> bool:
        if not self.headed or self._runtime is None:
            return False

        input(
            "Open ChatGPT Settings > アカウント > 支払い > 管理する in the browser, "
            "wait for the Stripe billing portal to appear, then press Enter to continue..."
        )
        for _ in range(10):
            billing_page = self._resolve_billing_page()
            if billing_page is not None:
                self._runtime = PortalRuntime(
                    browser=self._runtime.browser,
                    context=self._runtime.context,
                    page=billing_page,
                )
                return True
            for candidate in reversed(self._runtime.context.pages):
                if candidate is self.page:
                    continue
                candidate.wait_for_timeout(250)
                candidate_text = ""
                try:
                    candidate_text = candidate.locator("body").inner_text()
                except Exception:
                    candidate_text = ""
                if (
                    "OpenAI OpCo, LLC" in candidate_text
                    or "Powered by stripe" in candidate_text
                    or "請求書をダウンロード" in candidate_text
                    or "領収書をダウンロード" in candidate_text
                    or "Manage your OpenAI billing settings" in candidate_text
                ):
                    self._runtime = PortalRuntime(
                        browser=self._runtime.browser,
                        context=self._runtime.context,
                        page=candidate,
                    )
                    return True
            self.page.wait_for_timeout(500)

        return False

    def assert_session_valid(self) -> None:
        page = self.page
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        _wait_for_chatgpt_shell(page, timeout=self.settings.playwright_timeout_ms)

        authenticated_locators = [
            page.get_by_role("button", name=re.compile("new chat|新しいチャット", re.IGNORECASE)),
            page.get_by_role("link", name=re.compile("new chat|新しいチャット", re.IGNORECASE)),
            page.get_by_role("button", name=re.compile("chatgpt", re.IGNORECASE)),
            page.get_by_text(re.compile("今日はどうしましたか？|what can i help with today", re.IGNORECASE)),
            page.get_by_text(re.compile("plus|pro|team|free", re.IGNORECASE)),
        ]
        if not any(locator.count() for locator in authenticated_locators):
            self.save_debug_artifact("session-expired")
            raise SessionExpiredError(
                "Saved session is not authenticated. Run `uv run invoice-downloader prepare-session --headed`."
            )

    def open_billing_history(self) -> None:
        page = self.page
        timeout = self.settings.playwright_timeout_ms

        try:
            billing_url = self._fetch_customer_portal_url()
            page.goto(billing_url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(1500)
        except (TimeoutError, Error, InvoiceDownloaderError) as exc:
            if self._prompt_for_manual_billing_navigation():
                return
            self.save_debug_artifact("billing-navigation-failed")
            raise BillingHistoryNotFoundError(
                "Could not reach the billing portal from ChatGPT session."
            ) from exc

    def _fetch_customer_portal_url(self) -> str:
        token = self.settings.chatgpt_access_token or self._extract_access_token()
        if not token:
            raise BillingHistoryNotFoundError("Could not extract a ChatGPT access token from the saved session.")

        page = self.page
        payload = page.evaluate(
            """async (accessToken) => {
              const endpoints = [
                { method: "POST", url: "/backend-api/payments/customer_portal" },
                { method: "GET", url: "/backend-api/payments/customer_portal" },
              ];
              for (const endpoint of endpoints) {
                try {
                  const response = await fetch(endpoint.url, {
                    method: endpoint.method,
                    credentials: "include",
                    headers: {
                      "Authorization": `Bearer ${accessToken}`,
                      "Accept": "application/json",
                      "Content-Type": "application/json",
                    },
                  });
                  const text = await response.text();
                  let data = null;
                  try {
                    data = JSON.parse(text);
                  } catch (_) {
                    data = null;
                  }
                  if (response.ok && data && typeof data.url === "string" && data.url) {
                    return { ok: true, url: data.url };
                  }
                  if (response.ok && data && typeof data.portal_url === "string" && data.portal_url) {
                    return { ok: true, url: data.portal_url };
                  }
                } catch (_) {}
              }
              return { ok: false };
            }""",
            token,
        )
        if not payload or not payload.get("ok") or not payload.get("url"):
            raise BillingHistoryNotFoundError("Could not resolve the Stripe customer portal URL from ChatGPT.")
        return str(payload["url"])

    def _extract_access_token(self) -> str | None:
        page = self.page
        token = page.evaluate(
            """async () => {
              const trySession = async () => {
                try {
                  const response = await fetch("/api/auth/session", {
                    method: "GET",
                    credentials: "include",
                    headers: { "Accept": "application/json" },
                  });
                  if (!response.ok) return null;
                  const data = await response.json();
                  return data && typeof data.accessToken === "string" ? data.accessToken : null;
                } catch (_) {
                  return null;
                }
              };

              const fromBootstrap = () => {
                const node = document.querySelector("#__NEXT_DATA__");
                if (!node || !node.textContent) return null;
                try {
                  const data = JSON.parse(node.textContent);
                  return data?.props?.pageProps?.session?.accessToken || null;
                } catch (_) {
                  return null;
                }
              };

              return (await trySession()) || fromBootstrap();
            }"""
        )
        return str(token).strip() if token else None

    def list_invoice_candidates(self) -> list[InvoiceCandidate]:
        page = self._billing_target()
        direct_links = page.get_by_role("link")
        link_candidates: dict[str, InvoiceCandidate] = {}
        try:
            link_count = direct_links.count()
        except Error:
            link_count = 0
        for index in range(link_count):
            link = direct_links.nth(index)
            try:
                href = link.get_attribute("href")
                text = link.inner_text().strip()
            except Error:
                continue
            if not href or "invoice.stripe.com" not in href or not text:
                continue
            normalized = text.lower()
            if "chatgpt plus subscription" not in normalized and not re.search(r"20\d{2}[/-]\d{2}[/-]\d{2}", text):
                continue
            candidate = InvoiceCandidate(row_text=text, href=href)
            link_candidates[candidate.invoice_key] = candidate
        if link_candidates:
            return list(link_candidates.values())

        rows = [
            page.get_by_role("row"),
            page.get_by_role("button"),
            page.locator("tr"),
            page.locator("[role='listitem']"),
            page.locator("[data-testid*='invoice'], [data-testid*='payment']"),
            page.locator("li"),
        ]

        candidates: list[InvoiceCandidate] = []
        for locator in rows:
            try:
                count = locator.count()
            except Error:
                continue
            for index in range(count):
                row = locator.nth(index)
                text = row.inner_text().strip()
                if not text:
                    continue
                normalized = text.lower()
                if (
                    "invoice" not in normalized
                    and "receipt" not in normalized
                    and "chatgpt plus subscription" not in normalized
                    and not re.search(r"20\d{2}[/-]\d{2}[/-]\d{2}", text)
                ):
                    continue
                href = None
                try:
                    href = row.get_attribute("href")
                except Error:
                    href = None
                if not href:
                    link_locator = row.locator("a").first
                    if link_locator.count():
                        href = link_locator.get_attribute("href")
                candidates.append(InvoiceCandidate(row_text=text, href=href))
            if candidates:
                break

        unique_candidates = {candidate.invoice_key: candidate for candidate in candidates}
        if unique_candidates:
            return list(unique_candidates.values())

        page_text = page.locator("body").inner_text().strip()
        has_download_button = any(
            locator.count()
            for locator in [
                page.get_by_role("button", name=re.compile("請求書をダウンロード", re.IGNORECASE)),
                page.get_by_role("button", name=re.compile("領収書をダウンロード", re.IGNORECASE)),
                page.get_by_role("link", name=re.compile("請求書をダウンロード", re.IGNORECASE)),
                page.get_by_role("link", name=re.compile("領収書をダウンロード", re.IGNORECASE)),
            ]
        )
        if has_download_button and page_text:
            return [InvoiceCandidate(row_text=page_text)]

        return []

    def download_invoice(self, candidate: InvoiceCandidate, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        billing_target = self._billing_target()
        try:
            download_trigger = self._first_available_locator(
                self._download_button_factories(billing_target, candidate)
            )
        except TimeoutError:
            invoice_link = candidate.href
            if invoice_link:
                self.page.goto(invoice_link, wait_until="domcontentloaded", timeout=self.settings.playwright_timeout_ms)
                _wait_for_stripe_invoice_page(self.page, timeout=self.settings.playwright_timeout_ms)
                billing_target = self._billing_target()
                try:
                    download_trigger = self._first_available_locator(
                        self._download_button_factories(billing_target, candidate)
                    )
                except TimeoutError as exc:
                    self.save_debug_artifact("download-button-not-found")
                    raise InvoiceDownloadError(
                        f"Failed to find a download button for invoice {candidate.invoice_key}"
                    ) from exc
            else:
                lines = [line.strip() for line in candidate.row_text.splitlines() if line.strip()]
                date_fragment = lines[0] if lines else candidate.row_text
                amount_fragment = next((line for line in lines if line.startswith("$")), "")
                row_trigger = self._first_available_locator(
                    [
                        lambda: billing_target.get_by_role("link", name=re.compile(re.escape(date_fragment), re.IGNORECASE)),
                        lambda: billing_target.get_by_text(re.compile(re.escape(date_fragment), re.IGNORECASE)),
                        lambda: billing_target.get_by_text(re.compile(re.escape(amount_fragment), re.IGNORECASE)) if amount_fragment else billing_target.locator(".__never__"),
                        lambda: billing_target.get_by_role("button", name=re.compile(re.escape(date_fragment), re.IGNORECASE)),
                    ]
                )
                row_trigger.click(timeout=self.settings.playwright_timeout_ms)
                _wait_for_stripe_invoice_page(self.page, timeout=self.settings.playwright_timeout_ms)
                billing_target = self._billing_target()
                try:
                    download_trigger = self._first_available_locator(
                        self._download_button_factories(billing_target, candidate)
                    )
                except TimeoutError as exc:
                    self.save_debug_artifact("download-button-not-found")
                    raise InvoiceDownloadError(
                        f"Failed to find a download button for invoice {candidate.invoice_key}"
                    ) from exc

        try:
            with self.page.expect_download(timeout=self.settings.playwright_timeout_ms) as download_info:
                download_trigger.click()
            download = download_info.value
            download.save_as(str(destination))
        except Exception as exc:
            self.save_debug_artifact("download-failed")
            raise InvoiceDownloadError(f"Failed to download invoice {candidate.invoice_key}") from exc

    def _download_button_factories(self, billing_target: Page | Frame, candidate: InvoiceCandidate):
        return [
            lambda: billing_target.locator("button.Button--primary"),
            lambda: billing_target.locator("a.Button--primary"),
            lambda: billing_target.get_by_role("button", name=re.compile("領収書をダウンロード", re.IGNORECASE)),
            lambda: billing_target.get_by_role("link", name=re.compile("領収書をダウンロード", re.IGNORECASE)),
            lambda: billing_target.get_by_role("button", name=re.compile("請求書をダウンロード", re.IGNORECASE)),
            lambda: billing_target.get_by_role("link", name=re.compile("請求書をダウンロード", re.IGNORECASE)),
            lambda: billing_target.get_by_role("button", name=re.compile("invoice|receipt|pdf", re.IGNORECASE)),
            lambda: billing_target.get_by_role("link", name=re.compile("invoice|receipt|pdf", re.IGNORECASE)),
            lambda: billing_target.get_by_text(re.compile(re.escape(candidate.row_text), re.IGNORECASE)),
        ]

    def _first_available_locator(self, factories):
        for factory in factories:
            locator = factory()
            try:
                if locator.count():
                    return locator.first
            except Error:
                continue
        raise TimeoutError("No matching locator found")

    def _click_first_available(self, factories, *, timeout: int) -> None:
        self._first_available_locator(factories).click(timeout=timeout)


def prepare_session(settings: Settings, *, headed: bool) -> Path:
    settings.ensure_runtime_paths()
    user_data_dir = settings.storage_state_path.parent / "chrome-profile"
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            accept_downloads=True,
            **_playwright_launch_kwargs(settings, headed=headed),
        )
        page = context.new_page()
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        input(
            "Finish logging in to ChatGPT in the opened browser, then press Enter to save the session..."
        )
        context.storage_state(path=str(settings.storage_state_path))
        context.close()
    return settings.storage_state_path


def run_download_flow(
    *,
    settings: Settings,
    portal: ChatGPTPortal,
    manifest: InvoiceManifest,
    limit: int | None = None,
    dry_run: bool = False,
) -> DownloadSummary:
    if not settings.storage_state_path.exists():
        raise SessionStateMissingError(
            "Saved session file is missing. Run `uv run invoice-downloader prepare-session --headed`."
        )

    portal.assert_session_valid()
    portal.open_billing_history()
    candidates = portal.list_invoice_candidates()
    if not candidates:
        portal.save_debug_artifact("invoice-history-empty")
        raise BillingHistoryNotFoundError("No invoice history rows were found.")

    pending = [candidate for candidate in candidates if not manifest.has(candidate.invoice_key)]
    if limit is not None:
        pending = pending[:limit]

    downloaded_count = 0
    if not dry_run:
        for candidate in pending:
            filename = build_invoice_filename(
                candidate.row_text,
                candidate.href,
                account_label=settings.account_label,
            )
            destination = settings.download_dir / filename
            portal.download_invoice(candidate, destination)
            drive_upload = None
            uploaded_to_drive_at = None
            if drive_upload_enabled(settings):
                drive_upload = upload_to_drive(settings, destination, filename)
                uploaded_to_drive_at = datetime.now(UTC).isoformat()
            manifest.add(
                row_text=candidate.row_text,
                href=candidate.href,
                filename=filename,
                drive_file_id=(drive_upload or {}).get("id"),
                drive_filename=(drive_upload or {}).get("name"),
                uploaded_to_drive_at=uploaded_to_drive_at,
            )
            downloaded_count += 1
        manifest.save()

    return DownloadSummary(
        discovered_count=len(candidates),
        pending_count=len(pending),
        downloaded_count=downloaded_count,
        dry_run=dry_run,
    )


def sync_saved_invoices(
    *,
    settings: Settings,
    manifest: InvoiceManifest,
) -> StorageSyncSummary:
    renamed_count = 0
    uploaded_count = 0

    for invoice_key, entry in list(manifest.entries.items()):
        expected_filename = build_invoice_filename(
            entry.row_text,
            entry.href,
            account_label=settings.account_label,
        )
        current_path = settings.download_dir / entry.filename
        expected_path = settings.download_dir / expected_filename

        if entry.filename != expected_filename and current_path.exists():
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            current_path.rename(expected_path)
            manifest.update(invoice_key, filename=expected_filename)
            manifest.save()
            renamed_count += 1
        elif entry.filename != expected_filename and expected_path.exists():
            manifest.update(invoice_key, filename=expected_filename)
            manifest.save()
            renamed_count += 1

        active_entry = manifest.entries[invoice_key]
        active_path = settings.download_dir / active_entry.filename
        if (
            drive_upload_enabled(settings)
            and not active_entry.drive_file_id
            and active_path.exists()
        ):
            drive_upload = upload_to_drive(settings, active_path, active_entry.filename)
            manifest.update(
                invoice_key,
                drive_file_id=drive_upload.get("id"),
                drive_filename=drive_upload.get("name"),
                uploaded_to_drive_at=datetime.now(UTC).isoformat(),
            )
            manifest.save()
            uploaded_count += 1

    return StorageSyncSummary(
        renamed_count=renamed_count,
        uploaded_count=uploaded_count,
    )


def download_invoices(
    settings: Settings,
    *,
    headed: bool | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> DownloadSummary:
    manifest = InvoiceManifest.load(settings.invoice_manifest_path)
    with PlaywrightChatGPTPortal(settings, headed=headed) as portal:
        return run_download_flow(
            settings=settings,
            portal=portal,
            manifest=manifest,
            limit=limit,
            dry_run=dry_run,
        )
