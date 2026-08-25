# Book Publisher Monitor — GitHub Actions + Google Sheets

This monitor runs entirely from GitHub Actions. It does **not** require Google Apps Script. Every 24 hours, GitHub Actions reads the combined `Products` tab from the connected Google Sheet, fetches each product page, compares `Price Before`, `Price After`, and `Stock`, updates successful values, and sends one Telegram message for every actual field change.

## Repository contents

| File | Purpose |
|---|---|
| `monitor.py` | Main Python monitor using the Google Sheets API and Telegram Bot API. |
| `.github/workflows/monitor.yml` | Daily scheduled workflow and manual test workflow. |
| `Products_combined.csv` | Combined inventory containing 3,060 products. |
| `requirements.txt` | Python dependencies installed by GitHub Actions. |
| `appsscript.json` | Legacy manifest retained only for reference; it is not used by the workflow. |
| `Code.gs` | Legacy Apps Script version retained for reference; it is not used by the workflow. |

## Google Sheet

The workflow is configured for spreadsheet ID:

```text
110ZPf1PpYtMO_MMYqF6tD3A-jgbgPacbPOlQax7yatk
```

Create one tab named `Products` and import `Products_combined.csv`. Keep these headers:

```text
Product Name,Product URL,Price Before,Price After,Stock,Publisher
```

## One-time Google API connection

GitHub needs permission to read and edit the Sheet. Create a Google Cloud service account with the Google Sheets API enabled, download its JSON key, and share the Google Sheet with the service-account email as an **Editor**. The JSON key must not be committed to GitHub.

In the repository, open **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The complete contents of the service-account JSON file. |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token. |
| `TELEGRAM_CHAT_ID` | The destination Telegram chat ID. |

The spreadsheet ID is already configured in `.github/workflows/monitor.yml`, so it does not need to be stored as a secret.

## Test procedure

Open the repository’s **Actions** tab, select **Book publisher monitor**, click **Run workflow**, and enter `10` in the optional `limit` field. This checks only the first ten products. Leave the field empty to check all products.

The monitor updates successful rows and creates or updates a `Monitor Errors` tab for failed requests or parsing. Errors are never sent to Telegram. Review that tab and the workflow logs. Some publisher websites may block GitHub requests or require JavaScript rendering; those failures must be fixed in the parser or with an approved fetch method before unattended monitoring is considered reliable.

## Daily operation

The workflow is scheduled with the cron expression `0 0 * * *`, which starts once per day at 00:00 UTC. GitHub may start a scheduled workflow slightly later. It can also be started manually from the Actions tab.

Each detected change produces its own Telegram message containing the publisher, product name, changed field, old value, new value, and product URL. A price-before change and a stock change for the same book therefore produce two separate messages.

## Security

Never commit the Google service-account JSON, Telegram bot token, or Telegram chat ID. Store them only in GitHub Actions repository secrets. If a credential is ever exposed, revoke it and create a replacement immediately.
