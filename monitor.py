import html
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import gspread
import requests
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup

SHEET_ID = os.environ['GOOGLE_SHEET_ID']
SHEET_NAME = os.getenv('SHEET_NAME', 'Products')
ERROR_SHEET_NAME = os.getenv('ERROR_SHEET_NAME', 'Monitor Errors')
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
LIMIT = int(os.getenv('MONITOR_LIMIT', '0'))
PUBLISHER_FILTER = os.getenv('PUBLISHER_FILTER', 'all').strip().lower()
REQUEST_TIMEOUT = 30
SLEEP_SECONDS = 0.25
SHEET_WRITE_BATCH_SIZE = 50
HEADERS = ['Product Name', 'Product URL', 'Price Before', 'Price After', 'Stock', 'Publisher']


def number(value: Any):
    if value is None or str(value).strip() == '':
        return None
    s = str(value).translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
    # Extract the first complete numeric token. Stripping all non-numeric
    # characters would turn the dot in Arabic currency text (ج.م) into a
    # trailing decimal point, e.g. `486.00 ج.م` -> `486.00.`.
    match = re.search(r'-?[0-9]+(?:[.,][0-9]+)*', s)
    if not match:
        return None
    s = match.group(0).replace(',', '')
    try:
        return float(s)
    except ValueError:
        return None


def stock_normalize(value: Any) -> str:
    return str(value or '').strip()


def decode_text(value: str) -> str:
    return html.unescape(BeautifulSoup(value or '', 'html.parser').get_text(' ', strip=True))


def parse_json_ld(soup: BeautifulSoup) -> list[dict]:
    items = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and isinstance(item.get('@graph'), list):
                items.extend(x for x in item['@graph'] if isinstance(x, dict))
            elif isinstance(item, dict):
                items.append(item)
    return items


def first_number(soup: BeautifulSoup, text: str, selectors: list[str], labels: list[str]):
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = number(node.get_text(' ', strip=True))
            if value is not None:
                return value
    for label in labels:
        match = re.search(label + r'[^0-9٠-٩]{0,80}([0-9٠-٩][0-9٠-٩,.]*)', text, re.I)
        if match:
            value = number(match.group(1))
            if value is not None:
                return value
    return None


def parse_product(response: requests.Response) -> dict:
    soup = BeautifulSoup(response.text, 'html.parser')
    text = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True))
    raw_html = response.text
    json_ld = parse_json_ld(soup)
    before = None
    after = None
    egp_marked = bool(re.search(r'\bEGP\b|ج\.م|جنيه|جنية|جنيه مصري|Egyptian Pound|LE\b', raw_html, re.I))
    egp_json_prices = []

    for item in json_ld:
        if item.get('@type') != 'Product':
            continue
        offers = item.get('offers')
        offer_list = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
        for offer in offer_list:
            specifications = offer.get('priceSpecification') if isinstance(offer, dict) else None
            specifications = specifications if isinstance(specifications, list) else [specifications] if isinstance(specifications, dict) else []
            for spec in specifications:
                if str(spec.get('priceCurrency') or '').upper() in ('EGP', 'ج.م') and number(spec.get('price')) is not None:
                    egp_marked = True
                    egp_json_prices.append(number(spec.get('price')))
            # Do not use offer.price here. On some multi-currency pages it is
            # a converted display value even when mislabeled as EGP. Only an
            # explicit EGP priceSpecification is accepted as authoritative.

    if 'thebookhome.com' in str(getattr(response, 'url', '')):
        # بيت الكتب exposes native EGP values in the main product block after
        # the session currency is set: .priceBefore and .currentPrice.
        price_box = soup.select_one('.single-product-price')
        if price_box and re.search(r'ج\.م|جنيه|EGP', price_box.get_text(' ', strip=True), re.I):
            before_node = price_box.select_one('.priceBefore')
            after_node = price_box.select_one('.currentPrice')
            before = number(before_node.get_text(' ', strip=True)) if before_node else None
            after = number(after_node.get_text(' ', strip=True)) if after_node else None

    if egp_json_prices and before is None and after is None:
        # Prefer explicit priceSpecification values over converted/display fallback values.
        after = egp_json_prices[0]

    if egp_marked and not egp_json_prices:
        egp_pattern = re.compile(r'\bEGP\b|ج\.م|جنيه|جنية|جنيه مصري|Egyptian Pound|LE\b', re.I)
        for selector in ['.sale-price', '.price-final', '.special-price', '.current-price', '[itemprop="price"]', 'ins .amount', 'ins']:
            node = soup.select_one(selector)
            node_text = node.get_text(' ', strip=True) if node else ''
            if node_text and egp_pattern.search(node_text):
                after = number(node_text)
                if after is not None:
                    break
        for selector in ['.regular-price', '.old-price', '.price-before', 'del .amount', 'del']:
            node = soup.select_one(selector)
            node_text = node.get_text(' ', strip=True) if node else ''
            if node_text and egp_pattern.search(node_text):
                before = number(node_text)
                if before is not None:
                    break
        # Do not fall back to generic .woocommerce-Price-amount nodes: on
        # multi-currency pages they often contain USD conversion values.

    if before is None and after is not None:
        before = after
    if after is None and before is not None:
        after = before

    availability = None
    for item in json_ld:
        offers = item.get('offers') if isinstance(item, dict) else None
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict) and offers.get('availability'):
            availability = str(offers['availability']).lower()
            break
    if availability and 'outofstock' in availability:
        stock = 'Out of Stock'
    elif availability and ('instock' in availability or 'limited' in availability):
        stock = 'In Stock'
    elif re.search(r'out of stock|out-of-stock|غير متوفر|غير متاح|نفذت الكمية|غير موجود بالمخزن', text, re.I):
        stock = 'Out of Stock'
    elif re.search(r'in stock|instock|متوفر|متاح|أضف إلى السلة|اضف الى السلة|add to cart', text, re.I):
        stock = 'In Stock'
    elif re.search(r'pre.?order|طلب مسبق|قريبا', text, re.I):
        stock = 'Pre-Order'
    else:
        stock = None

    if before is None and after is None and stock is None:
        raise ValueError('Could not detect EGP price or stock markers')
    return {'before': before, 'after': after, 'stock': stock}


def fetch_product(url: str) -> dict:
    request_headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; BookPublisherMonitor/1.0)',
        'Accept-Language': 'ar,en;q=0.8',
    }
    if 'elainpublishinghouse.com' in url and 'wmc-currency=' not in url:
        # دار العين uses WooCommerce Multi-Currency. Request native EGP.
        url += ('&' if '?' in url else '?') + 'wmc-currency=EGP'
        response = requests.get(url, headers=request_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    elif 'thebookhome.com' in url:
        # بيت الكتب stores the selected currency in a session cookie. Currency
        # value 1 is EGP; value 2 is USD. Establish the EGP session first.
        session = requests.Session()
        session.headers.update(request_headers)
        session.get('https://www.thebookhome.com/', timeout=REQUEST_TIMEOUT)
        currency_response = session.get(
            'https://www.thebookhome.com/Home/SetSelectedCurrency',
            params={'currency': '1'}, timeout=REQUEST_TIMEOUT
        )
        if currency_response.status_code >= 400:
            raise RuntimeError(f'Beit Al Kotob currency setup failed: HTTP {currency_response.status_code}')
        response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    else:
        response = requests.get(url, headers=request_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    if response.status_code >= 400:
        raise ValueError(f'HTTP {response.status_code}')
    return parse_product(response)


def send_telegram(product: str, publisher: str, url: str, changes: list[tuple[str, Any, Any]]):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError('Telegram secrets are not configured')
    change_lines = []
    for field, old, new in changes:
        change_lines.append(
            f'الحقل: {field}\n'
            f'القيمة القديمة: {old if old not in (None, "") else "blank"}\n'
            f'القيمة الجديدة: {new if new not in (None, "") else "blank"}'
        )
    message = (
        'تغيير في المنتج\n\n'
        f'الناشر: {publisher}\n'
        f'الكتاب: {product}\n\n'
        + '\n\n'.join(change_lines) + '\n\n'
        f'الرابط: {url}'
    )
    result = requests.post(
        f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
        data={'chat_id': CHAT_ID, 'text': message, 'disable_web_page_preview': 'false'},
        timeout=REQUEST_TIMEOUT,
    )
    result.raise_for_status()


def connect_sheet():
    raw = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
    if not raw:
        raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON is empty or missing. Add the complete JSON file contents as a GitHub Actions secret with this exact name.')
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the complete contents of the downloaded .json key file, not the filename, file path, or a Google Cloud URL.') from exc
    required = ['client_email', 'private_key', 'token_uri']
    missing = [key for key in required if not info.get(key)]
    if missing:
        raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON is missing required fields: ' + ', '.join(missing))
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def write_row_slice_(sheet, selected_rows, start_index: int, end_index: int, column_count: int):
    if end_index <= start_index:
        return
    end_column = column_letter(column_count)
    updates = [{
        'range': f'A{sheet_row}:{end_column}{sheet_row}',
        'values': [row]
    } for sheet_row, row in selected_rows[start_index:end_index]]
    sheet.batch_update(updates, raw=False)
    print(f'Updated {len(updates)} selected Sheet rows ({selected_rows[start_index][0]}-{selected_rows[end_index - 1][0]})')


def column_letter(number: int) -> str:
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def ensure_column(sheet, title: str):
    headers = [str(x).replace('\ufeff', '').strip().lower() for x in sheet.row_values(1)]
    key = title.lower()
    if key in headers:
        return headers.index(key)
    new_index = len(headers)
    sheet.update_cell(1, new_index + 1, title)
    return new_index


def ensure_error_sheet(book):
    try:
        tab = book.worksheet(ERROR_SHEET_NAME)
    except gspread.WorksheetNotFound:
        tab = book.add_worksheet(title=ERROR_SHEET_NAME, rows=1000, cols=5)
        tab.append_row(['Timestamp', 'Sheet Row', 'Product Name', 'URL', 'Error'])
    return tab


def main():
    client = connect_sheet()
    book = client.open_by_key(SHEET_ID)
    try:
        sheet = book.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheets = book.worksheets()
        if not worksheets:
            raise RuntimeError(f'No worksheets exist in the spreadsheet. Create a tab named {SHEET_NAME}.')
        sheet = worksheets[0]
        print(f'Worksheet {SHEET_NAME!r} was not found; using the first worksheet: {sheet.title!r}')
    values = sheet.get_all_values()
    if not values:
        raise RuntimeError(f'{SHEET_NAME} is empty')
    headers = [str(x).replace('\ufeff', '').strip().lower() for x in values[0]]
    status_col = ensure_column(sheet, 'Monitor Status')
    checked_col = ensure_column(sheet, 'Last Checked')
    if status_col is not None or checked_col is not None:
        values = sheet.get_all_values()
        headers = [str(x).replace('\ufeff', '').strip().lower() for x in values[0]]
    def col(*names):
        for name in names:
            if name.lower() in headers:
                return headers.index(name.lower())
        raise RuntimeError(f'Missing required column: {names}')
    ix = {
        'name': col('Product Name'), 'url': col('Product URL', 'Product Url'),
        'before': col('Price Before'), 'after': col('Price After'),
        'stock': col('Stock'), 'publisher': col('Publisher'),
        'status': col('Monitor Status'), 'checked': col('Last Checked')
    }
    error_tab = None
    error_rows = []
    checked = 0
    errors = 0
    flushed_to = 0
    selected_rows = []
    for offset, row in enumerate(values[1:], start=2):
        row += [''] * (len(headers) - len(row))
        url = str(row[ix['url']]).strip()
        publisher = str(row[ix['publisher']]).strip()
        if not url:
            continue
        if PUBLISHER_FILTER not in ('', 'all'):
            haystack = f'{publisher} {url}'.lower()
            if PUBLISHER_FILTER in ('karma', 'alkarma') and 'alkarmabooks.com' not in haystack:
                continue
            if PUBLISHER_FILTER in ('beit', 'beit alkotob', 'bookhome') and 'thebookhome.com' not in haystack:
                continue
        selected_rows.append((offset, row))
    if LIMIT:
        selected_rows = selected_rows[:LIMIT]
    print(f'Selected {len(selected_rows)} rows for publisher filter: {PUBLISHER_FILTER or "all"}')

    for selected_index, (offset, row) in enumerate(selected_rows):
        url = str(row[ix['url']]).strip()
        try:
            live = fetch_product(url)
            old = {'before': number(row[ix['before']]), 'after': number(row[ix['after']]), 'stock': stock_normalize(row[ix['stock']])}
            new = live
            changes = []
            if live['before'] is not None and old['before'] != live['before']:
                changes.append(('Price Before', old['before'], live['before']))
            if live['after'] is not None and old['after'] != live['after']:
                changes.append(('Price After', old['after'], live['after']))
            if live['stock'] and old['stock'] != live['stock']:
                changes.append(('Stock', old['stock'], live['stock']))

            if live['before'] is not None:
                row[ix['before']] = live['before']
            if live['after'] is not None:
                row[ix['after']] = live['after']
            if live['stock']:
                row[ix['stock']] = live['stock']

            checked += 1
            row[ix['status']] = 'OK'
            row[ix['checked']] = datetime.now(timezone.utc).isoformat()
            if changes:
                try:
                    send_telegram(str(row[ix['name']]), str(row[ix['publisher']]), url, changes)
                except Exception as telegram_exc:
                    # Sheet synchronization remains successful even if Telegram fails.
                    errors += 1
                    print(f'Row {offset} Telegram error: {type(telegram_exc).__name__}: {telegram_exc}')
                    error_rows.append([datetime.now(timezone.utc).isoformat(), offset, row[ix['name']], url, 'Telegram: ' + str(telegram_exc)])
        except Exception as exc:
            errors += 1
            row[ix['status']] = 'ERROR'
            row[ix['checked']] = datetime.now(timezone.utc).isoformat()
            print(f'Row {offset} monitor error: {type(exc).__name__}: {exc}')
            error_rows.append([datetime.now(timezone.utc).isoformat(), offset, row[ix['name']], url, str(exc)])
        time.sleep(SLEEP_SECONDS)
        current_end = selected_index + 1
        if current_end - flushed_to >= SHEET_WRITE_BATCH_SIZE:
            write_row_slice_(sheet, selected_rows, flushed_to, current_end, len(headers))
            flushed_to = current_end

    if flushed_to < len(selected_rows):
        write_row_slice_(sheet, selected_rows, flushed_to, len(selected_rows), len(headers))
    if error_rows:
        error_tab = ensure_error_sheet(book)
        error_tab.append_rows(error_rows, value_input_option='USER_ENTERED')
    print(f'Checked successfully: {checked}; errors logged silently: {errors}')


if __name__ == '__main__':
    main()
