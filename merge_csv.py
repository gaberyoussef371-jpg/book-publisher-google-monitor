import csv
from pathlib import Path

source_dir = Path('/home/ubuntu/upload')
output = Path('/home/ubuntu/BookPublisherMonitor/Products_combined.csv')
files = ['DARELEAIN.csv', 'darnahda.csv', 'DarelKARMA.csv', 'dardiwan.csv']
headers = ['Product Name', 'Product URL', 'Price Before', 'Price After', 'Stock', 'Publisher']

with output.open('w', encoding='utf-8-sig', newline='') as out:
    writer = csv.DictWriter(out, fieldnames=headers)
    writer.writeheader()
    total = 0
    for filename in files:
        with (source_dir / filename).open('r', encoding='utf-8-sig', newline='') as src:
            reader = csv.DictReader(src)
            for row in reader:
                url = row.get('Product URL') or row.get('Product Url') or ''
                writer.writerow({
                    'Product Name': row.get('Product Name', ''),
                    'Product URL': url,
                    'Price Before': row.get('Price Before', ''),
                    'Price After': row.get('Price After', ''),
                    'Stock': row.get('Stock', ''),
                    'Publisher': row.get('Publisher', ''),
                })
                total += 1
print(f'Merged {total} products into {output}')
