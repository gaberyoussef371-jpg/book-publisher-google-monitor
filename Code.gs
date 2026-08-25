/**
 * Book Publisher Monitor for Google Sheets + Telegram
 *
 * Sheet columns supported:
 * Product Name | Product URL (or Product Url) | Price Before | Price After | Stock | Publisher
 *
 * Behavior:
 * - Checks every non-empty product row on a 24-hour time trigger.
 * - Sends one Telegram message per successful price/stock change.
 * - Updates the row only after a successful parse.
 * - Does not send errors to Telegram. Errors are written to Monitor Errors.
 * - Uses structured data, WooCommerce selectors, meta tags, and visible text fallbacks.
 */

const CONFIG = {
  SHEET_NAME: 'Products',
  ERROR_SHEET_NAME: 'Monitor Errors',
  BOT_TOKEN: 'PASTE_TELEGRAM_BOT_TOKEN_HERE',
  CHAT_ID: 'PASTE_TELEGRAM_CHAT_ID_HERE',
  CHECK_INTERVAL_HOURS: 24,
  REQUEST_TIMEOUT_MS: 30000,
  SLEEP_BETWEEN_REQUESTS_MS: 250,
  INITIAL_SYNC_NO_ALERT: true,
  TEST_MAX_ROWS: 10,
  BATCH_SIZE: 75,
  CONTINUE_DELAY_MS: 60000,
  TEST_MODE: true,
  SEND_STOCK_ALERTS: true,
  SEND_PRICE_ALERTS: true,
  CURRENCY: 'EGP'
};

const HEADERS = ['Product Name', 'Product URL', 'Price Before', 'Price After', 'Stock', 'Publisher'];

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Book Monitor')
    .addItem('Run test (first 10 rows)', 'runTest')
    .addItem('Run full monitor now', 'runMonitor')
    .addItem('Install 24-hour trigger', 'setupDailyTrigger')
    .addItem('Remove monitor triggers', 'removeMonitorTriggers')
    .addToUi();
}

function runTest() {
  monitorProducts_(true);
}

function runMonitor() {
  PropertiesService.getScriptProperties().deleteProperty('MONITOR_NEXT_ROW');
  monitorProducts_(false, 2);
}

function runNextBatch_() {
  const props = PropertiesService.getScriptProperties();
  const nextRow = Number(props.getProperty('MONITOR_NEXT_ROW') || 2);
  monitorProducts_(false, nextRow);
}

function setupDailyTrigger() {
  removeMonitorTriggers();
  ScriptApp.newTrigger('runMonitor')
    .timeBased()
    .everyHours(CONFIG.CHECK_INTERVAL_HOURS)
    .create();
}

function removeMonitorTriggers() {
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    if (['runMonitor', 'runNextBatch_'].indexOf(trigger.getHandlerFunction()) >= 0) {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function monitorProducts_(isTest, startRow) {
  const lock = LockService.getScriptLock();
  const props = PropertiesService.getScriptProperties();
  lock.waitLock(30000);
  try {
    const sheet = getProductsSheet_();
    const map = getHeaderMap_(sheet);
    const lastRow = sheet.getLastRow();
    if (lastRow < 2) return;

    const firstRow = startRow || 2;
    const available = lastRow - firstRow + 1;
    const rowCount = isTest ? Math.min(CONFIG.TEST_MAX_ROWS, available) : Math.min(CONFIG.BATCH_SIZE, available);
    if (rowCount <= 0) return;
    const values = sheet.getRange(firstRow, 1, rowCount, sheet.getLastColumn()).getValues();
    let processed = 0;

    values.forEach(function(row, index) {
      const rowNumber = index + firstRow;
      const url = String(row[map.url] || '').trim();
      if (!url) return;

      try {
        const result = fetchAndParseProduct_(url);
        if (!result.ok) {
          logError_(rowNumber, row[map.name], url, result.error || 'Parser returned no result');
          return;
        }

        const oldValues = {
          before: normalizeNumber_(row[map.before]),
          after: normalizeNumber_(row[map.after]),
          stock: normalizeStock_(row[map.stock])
        };
        const newValues = {
          before: result.priceBefore,
          after: result.priceAfter,
          stock: result.stock
        };

        const isFirstBaseline = CONFIG.INITIAL_SYNC_NO_ALERT && isBlankProductState_(oldValues);
        const changes = getChanges_(oldValues, newValues);

        // Do not overwrite a field when the parser could not find that field.
        if (result.priceBefore !== null) sheet.getRange(rowNumber, map.before + 1).setValue(result.priceBefore);
        if (result.priceAfter !== null) sheet.getRange(rowNumber, map.after + 1).setValue(result.priceAfter);
        if (result.stock && result.stock !== 'Unknown') sheet.getRange(rowNumber, map.stock + 1).setValue(result.stock);

        if (!isFirstBaseline && changes.length > 0) {
          changes.forEach(function(change) {
            sendTelegramChange_(row, map, url, change, result);
          });
        }
        processed++;
        Utilities.sleep(CONFIG.SLEEP_BETWEEN_REQUESTS_MS);
      } catch (err) {
        logError_(rowNumber, row[map.name], url, String(err && err.message || err));
      }
    });

    const nextRow = firstRow + rowCount;
    if (!isTest && nextRow <= lastRow) {
      props.setProperty('MONITOR_NEXT_ROW', String(nextRow));
      ScriptApp.newTrigger('runNextBatch_').timeBased().after(CONFIG.CONTINUE_DELAY_MS).create();
    } else {
      props.deleteProperty('MONITOR_NEXT_ROW');
      logErrorSummary_('Run completed: ' + processed + ' product(s) processed successfully.');
    }
  } finally {
    lock.releaseLock();
  }
}

function getProductsSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(CONFIG.SHEET_NAME) || ss.getSheets()[0];
  if (!sheet) throw new Error('Products sheet was not found.');
  return sheet;
}

function getHeaderMap_(sheet) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(function(v) {
    return String(v).replace(/^\uFEFF/, '').trim().toLowerCase();
  });
  function find(names) {
    for (let i = 0; i < names.length; i++) {
      const pos = headers.indexOf(names[i].toLowerCase());
      if (pos >= 0) return pos;
    }
    throw new Error('Missing required column: ' + names.join(' / '));
  }
  return {
    name: find(['product name']),
    url: find(['product url', 'product Url'.toLowerCase()]),
    before: find(['price before']),
    after: find(['price after']),
    stock: find(['stock']),
    publisher: find(['publisher'])
  };
}

function fetchAndParseProduct_(url) {
  const response = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    followRedirects: true,
    validateHttpsCertificates: true,
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; GoogleAppsScript BookMonitor/1.0)',
      'Accept-Language': 'ar,en;q=0.8'
    }
  });
  const code = response.getResponseCode();
  const html = response.getContentText('UTF-8');
  if (code < 200 || code >= 400) return {ok: false, error: 'HTTP ' + code};
  if (!html || html.length < 200) return {ok: false, error: 'Empty or incomplete HTML'};

  const title = firstMatch_(html, [/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)/i, /<title[^>]*>([\s\S]*?)<\/title>/i]);
  const jsonLd = extractJsonLdProducts_(html);
  const prices = parsePrices_(html, jsonLd);
  const stock = parseStock_(html, jsonLd);

  if (prices.before === null && prices.after === null && !stock) {
    return {ok: false, error: 'Could not detect price or stock markers'};
  }
  return {
    ok: true,
    title: decodeHtml_(title || ''),
    priceBefore: prices.before,
    priceAfter: prices.after,
    stock: stock || 'Unknown'
  };
}

function extractJsonLdProducts_(html) {
  const output = [];
  const blocks = html.match(/<script[^>]+type=["']application\/ld\+json["'][^>]*>[\s\S]*?<\/script>/gi) || [];
  blocks.forEach(function(block) {
    const text = block.replace(/<\/script>[\s\S]*$/i, '').replace(/^[\s\S]*?>/, '').trim();
    try {
      const parsed = JSON.parse(text);
      const items = Array.isArray(parsed) ? parsed : [parsed];
      items.forEach(function(item) {
        if (item && item['@graph']) item['@graph'].forEach(function(x) { output.push(x); });
        else output.push(item);
      });
    } catch (e) {}
  });
  return output;
}

function parsePrices_(html, jsonLd) {
  let before = null, after = null;
  jsonLd.forEach(function(item) {
    if (!item || item['@type'] !== 'Product') return;
    const offers = item.offers && (Array.isArray(item.offers) ? item.offers[0] : item.offers);
    if (offers) {
      const current = numberFrom_(offers.price || offers.lowPrice);
      if (current !== null) after = current;
    }
  });
  const sale = firstNumber_(html, [
    /class=["'][^"']*(?:sale-price|price-final|special-price|current-price)[^"']*["'][^>]*>[\s\S]{0,300}?([0-9٠-٩][0-9٠-٩,.]*)/i,
    /(?:السعر بعد الخصم|السعر الحالي|السعر|current price|sale price)[^0-9٠-٩]{0,80}([0-9٠-٩][0-9٠-٩,.]*)/i
  ]);
  const regular = firstNumber_(html, [
    /class=["'][^"']*(?:regular-price|old-price|price-before|del)[^"']*["'][^>]*>[\s\S]{0,300}?([0-9٠-٩][0-9٠-٩,.]*)/i,
    /(?:السعر قبل الخصم|السعر الأصلي|regular price|old price)[^0-9٠-٩]{0,80}([0-9٠-٩][0-9٠-٩,.]*)/i
  ]);
  if (sale !== null) after = sale;
  if (regular !== null) before = regular;
  if (before === null && after !== null) before = after;
  if (after === null && before !== null) after = before;
  return {before: before, after: after};
}

function parseStock_(html, jsonLd) {
  for (let i = 0; i < jsonLd.length; i++) {
    const item = jsonLd[i];
    const offers = item && item.offers && (Array.isArray(item.offers) ? item.offers[0] : item.offers);
    if (offers && offers.availability) return availabilityToStock_(offers.availability);
  }
  const text = decodeHtml_(html.replace(/<script[\s\S]*?<\/script>/gi, ' ').replace(/<style[\s\S]*?<\/style>/gi, ' ').replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ');
  if (/(?:out of stock|out-of-stock|غير متوفر|غير متاح|نفذت الكمية|غير موجود بالمخزن)/i.test(text)) return 'Out of Stock';
  if (/(?:in stock|instock|متوفر|متاح|أضف إلى السلة|اضف الى السلة|add to cart)/i.test(text)) return 'In Stock';
  if (/(?:pre.?order|طلب مسبق|قريبا)/i.test(text)) return 'Pre-Order';
  return null;
}

function availabilityToStock_(value) {
  value = String(value).toLowerCase();
  if (value.indexOf('outofstock') >= 0) return 'Out of Stock';
  if (value.indexOf('preorder') >= 0) return 'Pre-Order';
  if (value.indexOf('instock') >= 0 || value.indexOf('limited') >= 0) return 'In Stock';
  return 'Unknown';
}

function getChanges_(oldValues, newValues) {
  const changes = [];
  if (CONFIG.SEND_PRICE_ALERTS && newValues.before !== null && !sameNumber_(oldValues.before, newValues.before)) changes.push({field: 'Price Before', oldValue: oldValues.before, newValue: newValues.before});
  if (CONFIG.SEND_PRICE_ALERTS && newValues.after !== null && !sameNumber_(oldValues.after, newValues.after)) changes.push({field: 'Price After', oldValue: oldValues.after, newValue: newValues.after});
  if (CONFIG.SEND_STOCK_ALERTS && newValues.stock && newValues.stock !== 'Unknown' && oldValues.stock !== newValues.stock) changes.push({field: 'Stock', oldValue: oldValues.stock || 'blank', newValue: newValues.stock});
  return changes;
}

function sendTelegramChange_(row, map, url, change, result) {
  if (!CONFIG.BOT_TOKEN || CONFIG.BOT_TOKEN.indexOf('PASTE_') === 0 || !CONFIG.CHAT_ID || CONFIG.CHAT_ID.indexOf('PASTE_') === 0) return;
  const product = String(row[map.name] || result.title || 'Unknown product');
  const publisher = String(row[map.publisher] || 'Unknown publisher');
  const message = 'تغيير في المنتج\n\n' +
    'الناشر: ' + publisher + '\n' +
    'الكتاب: ' + product + '\n' +
    'الحقل: ' + change.field + '\n' +
    'القيمة القديمة: ' + formatValue_(change.oldValue) + '\n' +
    'القيمة الجديدة: ' + formatValue_(change.newValue) + '\n' +
    'الرابط: ' + url;
  UrlFetchApp.fetch('https://api.telegram.org/bot' + encodeURIComponent(CONFIG.BOT_TOKEN) + '/sendMessage', {
    method: 'post', muteHttpExceptions: true,
    payload: {chat_id: CONFIG.CHAT_ID, text: message, disable_web_page_preview: false}
  });
}

function logError_(rowNumber, name, url, error) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(CONFIG.ERROR_SHEET_NAME);
  if (!sheet) { sheet = ss.insertSheet(CONFIG.ERROR_SHEET_NAME); sheet.appendRow(['Timestamp', 'Row', 'Product Name', 'URL', 'Error']); }
  sheet.appendRow([new Date(), rowNumber, name || '', url, error]);
}

function logErrorSummary_(message) {
  // Deliberately only records operational information in the spreadsheet; never sends it to Telegram.
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(CONFIG.ERROR_SHEET_NAME);
  if (!sheet) { sheet = ss.insertSheet(CONFIG.ERROR_SHEET_NAME); sheet.appendRow(['Timestamp', 'Row', 'Product Name', 'URL', 'Error']); }
  sheet.appendRow([new Date(), '', '', '', message]);
}

function normalizeNumber_(value) { return numberFrom_(value); }
function numberFrom_(value) {
  if (value === null || value === undefined || value === '') return null;
  const s = String(value).replace(/[٠-٩]/g, function(c) { return '٠١٢٣٤٥٦٧٨٩'.indexOf(c); }).replace(/[^0-9.,-]/g, '').replace(/,/g, '');
  const n = parseFloat(s);
  return isNaN(n) ? null : n;
}
function firstNumber_(html, patterns) { for (let i = 0; i < patterns.length; i++) { const m = html.match(patterns[i]); if (m) { const n = numberFrom_(m[1]); if (n !== null) return n; } } return null; }
function firstMatch_(html, patterns) { for (let i = 0; i < patterns.length; i++) { const m = html.match(patterns[i]); if (m) return m[1]; } return ''; }
function normalizeStock_(value) { return String(value || '').trim(); }
function sameNumber_(a, b) { return a !== null && b !== null && Number(a) === Number(b); }
function isBlankProductState_(v) { return v.before === null && v.after === null && !v.stock; }
function formatValue_(v) { return v === null || v === undefined || v === '' ? 'blank' : String(v) + (typeof v === 'number' ? ' ' + CONFIG.CURRENCY : ''); }
function decodeHtml_(s) { return String(s || '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&#39;|&apos;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>'); }
