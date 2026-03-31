# ChatGPT Invoice Downloader

CLI to download ChatGPT web subscription invoices with a saved browser session.

The downloader avoids the fragile ChatGPT settings UI. It uses an authenticated ChatGPT web session, resolves the Stripe customer portal URL, then downloads invoices from Stripe.

## Features

- `uv`-based project setup
- Saved ChatGPT browser session via Playwright
- `download --dry-run` to verify invoice discovery safely
- Deduplication with a JSON manifest
- Automatic filename normalization:
  `ACCOUNT_LABEL_invoice_YYYY-MM-DD_hash.pdf`
- Optional Google Drive upload
- `sync-storage` to rename older files and backfill missing Drive uploads

## Requirements

- Python 3.13+
- `uv`
- Local Chrome installed

## Setup

```bash
uv venv
uv sync --extra dev
uv run playwright install chromium
cp .env.example .env
```

## Configuration

Minimum `.env`:

```env
CHATGPT_STORAGE_STATE_PATH=.secrets/chatgpt-storage-state.json
DOWNLOAD_DIR=downloads
INVOICE_MANIFEST_PATH=downloads/invoice-manifest.json
PLAYWRIGHT_HEADLESS=false
PLAYWRIGHT_TIMEOUT_MS=15000
PLAYWRIGHT_BROWSER_CHANNEL=chrome
ACCOUNT_LABEL=chatgpt
```

Optional:

- `CHATGPT_ACCESS_TOKEN`
  Use this only if you want to bypass browser-side token extraction.
- `GOOGLE_OAUTH_CLIENT_SECRET_JSON`
- `GOOGLE_OAUTH_TOKEN_JSON`
- `GDRIVE_FOLDER_ID`

## Usage

Prepare a ChatGPT session:

```bash
uv run invoice-downloader prepare-session --headed
```

Verify invoice discovery without downloading:

```bash
uv run invoice-downloader download --headed --dry-run --limit 1
```

Download one invoice:

```bash
uv run invoice-downloader download --headed --limit 1
```

Download all pending invoices:

```bash
uv run invoice-downloader download
```

Rename older files and backfill storage metadata:

```bash
uv run invoice-downloader sync-storage
```

## Multi-Account

Use one `.env` file per account.

```bash
uv run invoice-downloader --env-file .env.account_a download
uv run invoice-downloader --env-file .env.account_b download
```

At minimum, separate these values per account:

- `CHATGPT_STORAGE_STATE_PATH`
- `DOWNLOAD_DIR`
- `INVOICE_MANIFEST_PATH`
- `ACCOUNT_LABEL`
- `CHATGPT_ACCESS_TOKEN` if used

## Google Drive

### Recommended simple option

If you use Google Drive for desktop, the simplest setup is to point `DOWNLOAD_DIR` and `INVOICE_MANIFEST_PATH` at a synced local Drive folder. In that case, no API setup is needed.

### OAuth upload to My Drive

For direct API upload to your own Drive:

1. Create a Google Cloud OAuth client for a Desktop app
2. Download the client secret JSON
3. Set these values in `.env`

```env
GOOGLE_OAUTH_CLIENT_SECRET_JSON=oauth_client_secret.json
GOOGLE_OAUTH_TOKEN_JSON=.secrets/google-drive-token.json
GDRIVE_FOLDER_ID=your_folder_id
```

4. Run the one-time OAuth flow:

```bash
uv run invoice-downloader prepare-drive-auth
```

After that, `download` and `sync-storage` can upload into your Drive folder.

### Service account

Service accounts are left supported in code, but they are usually not the right choice for a personal `My Drive`. Use OAuth or Google Drive for desktop unless you specifically need a Shared Drive setup.

## Tests

```bash
uv run pytest
RUN_MANUAL_E2E=1 uv run pytest -m manual_e2e
```

## Notes

- This tool is intended for ChatGPT web subscriptions billed through OpenAI/Stripe.
- App Store and Google Play subscriptions follow different billing flows.
- Session expiry is normal. If the saved session stops working, run `prepare-session` again.

## License

[MIT](LICENSE)
