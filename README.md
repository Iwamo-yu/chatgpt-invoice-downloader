# ChatGPT Invoice Downloader

ChatGPT Web購読の請求書をローカルへ取得するためのCLIです。
現在の正式な取得方式は、保存済みChatGPTセッションから access token を取り、`/backend-api/payments/customer_portal` で Stripe portal URL を取得して、Stripe 側から請求書を落とす方式です。

## Setup

```bash
uv venv
uv sync --extra dev
uv run playwright install chromium
cp .env.example .env
```

`.env` には保存先、セッションJSON、必要なら `CHATGPT_ACCESS_TOKEN` を書きます。
Google/Appleログインは `prepare-session` のときだけブラウザで手動対応します。
既定では Playwright bundled Chromium ではなく、ローカルの Chrome channel を使います。

ファイル名は自動で `ACCOUNT_LABEL_invoice_YYYY-MM-DD_hash.pdf` にリネームされます。
Google Drive へも保存したい場合は、個人の My Drive なら OAuth を使うのが自然です。`GOOGLE_OAUTH_CLIENT_SECRET_JSON` と `GDRIVE_FOLDER_ID` を設定し、最初に `prepare-drive-auth` を 1 回だけ実行してください。

## Usage

```bash
uv run invoice-downloader prepare-session --headed
uv run invoice-downloader prepare-drive-auth
uv run invoice-downloader download --headed --dry-run --limit 1
uv run invoice-downloader download --headed --limit 1
uv run invoice-downloader --env-file .env.work download --headed
uv run invoice-downloader sync-storage
```

`sync-storage` は既に落としてある PDF を新しい命名規則へ寄せて、Google Drive 未アップロード分だけ後追いで同期します。

## Google Drive OAuth

1. Google Cloud で OAuth クライアントを作る
2. ダウンロードした client secret JSON をこのプロジェクトに置く
3. `.env` に以下を書く

```env
GOOGLE_OAUTH_CLIENT_SECRET_JSON=oauth_client_secret.json
GOOGLE_OAUTH_TOKEN_JSON=.secrets/google-drive-token.json
GDRIVE_FOLDER_ID=your_folder_id
```

4. 初回だけ実行する

```bash
uv run invoice-downloader prepare-drive-auth
```

以後は `download` や `sync-storage` が保存済み OAuth token を使って My Drive にアップロードします。

## Multi-Account

アカウントごとに `.env` を分けるのが安全です。

```bash
uv run invoice-downloader --env-file .env.account_a download --headed
uv run invoice-downloader --env-file .env.account_b download --headed
```

最低限、以下はアカウントごとに分けてください。

- `CHATGPT_STORAGE_STATE_PATH`
- `DOWNLOAD_DIR`
- `INVOICE_MANIFEST_PATH`
- `ACCOUNT_LABEL`
- `CHATGPT_ACCESS_TOKEN` を使うならその値
- Google Drive も使うなら `GOOGLE_OAUTH_CLIENT_SECRET_JSON` と `GDRIVE_FOLDER_ID`

## Slack Bot Path

Slack bot 化するなら、この CLI をラップするのが最短です。

- Slack command / scheduled job から `uv run invoice-downloader --env-file ... download`
- 実行後に `downloads/` と `invoice-manifest.json` を要約して Slack に返す
- 複数アカウント運用は Slack workspace 側で `--env-file` を切り替える

## Tests

```bash
uv run pytest
RUN_MANUAL_E2E=1 uv run pytest -m manual_e2e
```
