import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

for path in Path('/home/ubuntu/BookPublisherMonitor/probes').glob('*.html'):
    soup = BeautifulSoup(path.read_text(encoding='utf-8', errors='ignore'), 'html.parser')
    candidates = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if isinstance(obj, dict) and isinstance(obj.get('@graph'), list):
                objects.extend(obj['@graph'])
        for obj in objects:
            if not isinstance(obj, dict) or obj.get('@type') != 'Product':
                continue
            offers = obj.get('offers')
            offers = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                candidates.append(('offer', offer.get('price'), offer.get('priceCurrency')))
                specs = offer.get('priceSpecification')
                specs = specs if isinstance(specs, list) else [specs] if isinstance(specs, dict) else []
                for spec in specs:
                    if isinstance(spec, dict):
                        candidates.append(('specification', spec.get('price'), spec.get('priceCurrency')))
    print(path.name)
    for candidate in candidates:
        print(' ', candidate)
