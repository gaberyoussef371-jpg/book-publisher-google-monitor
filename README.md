# Book Publisher Monitor for Google Apps Script

This package monitors the combined product list from four publisher websites in one Google Sheet tab. It compares the live `Price Before`, `Price After`, and `Stock` values against the stored row values, updates successful results, and sends a separate Telegram message for every detected change. Errors are recorded in `Monitor Errors` and are never sent to Telegram.

## Files

| File | Purpose |
|---|---|
| `Code.gs` | Complete Google Apps Script monitor. |
| `Products_combined.csv` | Merged inventory containing 3,060 products from the four supplied CSV files. |
| `merge_csv.py` | Reproducible script used to create the merged inventory. |

## Google Sheet setup

Create or open a Google Spreadsheet and create one tab named `Products`. Import `Products_combined.csv` into that tab. Keep the header row exactly as follows:

```text
Product Name,Product URL,Price Before,Price After,Stock,Publisher
```

The script also accepts `Product Url` for compatibility with the original `DARELEAIN.csv` spelling, but the merged file uses `Product URL` consistently.

## Apps Script setup

Open **Extensions → Apps Script**, create or open `Code.gs`, and paste the contents of the supplied `Code.gs` file. At the top of the file, replace the two placeholders:

```javascript
BOT_TOKEN: 'PASTE_TELEGRAM_BOT_TOKEN_HERE',
CHAT_ID: 'PASTE_TELEGRAM_CHAT_ID_HERE',
```

Do not share the token publicly. The bot must be able to send messages to the target chat. The currency label is configured as `EGP`; change it only if the sheet uses another currency.

## Safe testing sequence

Leave `TEST_MODE: true` and run `runTest` once from the Apps Script editor. This checks the first ten rows and creates `Monitor Errors` only when a row cannot be fetched or parsed. It does not send error messages. With `INITIAL_SYNC_NO_ALERT: true`, rows whose three tracked fields are all blank are initialized without generating alerts.

After reviewing the results, run `runMonitor` manually. The full monitor processes the inventory in safe batches of 75 rows and automatically schedules continuation batches until every product has been checked. This avoids one oversized Apps Script execution. A successful parse updates only the fields that were detected; a failed parse leaves the product row unchanged.

## Enable the 24-hour monitor

After the test results are reliable, run `setupDailyTrigger` once. This removes older monitor triggers and installs one time-based trigger that invokes `runMonitor` every 24 hours. The exact execution hour is selected by Google Apps Script within the project timezone. Set the project timezone under **Project Settings** before installing the trigger.

## Telegram message behavior

Each changed field generates its own message. For example, if a product’s price and stock both change, the monitor sends one message for the price change and another for the stock change. Each message includes the publisher, book name, changed field, old value, new value, and product URL. No combined daily summary is sent.

## Error behavior

HTTP failures, incomplete pages, and pages without recognizable product data are written to the `Monitor Errors` tab with a timestamp, row number, product name, URL, and error description. These failures do not update the product row and do not produce Telegram messages. Use this tab to identify site-specific selectors or access restrictions that need correction.

The parser first checks product structured data, then common WooCommerce and e-commerce HTML selectors, and finally Arabic/English visible-text markers. Some websites may block Google Apps Script or render product information only in a browser. In the initial probe, one publisher returned HTTP 403 and another returned an incomplete response, so the test run must be treated as a validation stage rather than assuming all four sites are already compatible with direct Apps Script requests. Those cases will appear in `Monitor Errors`; they should be fixed—possibly by adding a permitted fetch proxy or site-specific parser—before enabling unattended monitoring.
