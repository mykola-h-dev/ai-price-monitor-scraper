"""Normalise raw scraped strings into clean, typed values for a spreadsheet."""

import re
import unicodedata

RATING_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}


def clean_text(value):
    """Normalise unicode, collapse whitespace."""
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", value).strip()


def parse_price(raw):
    """'£51.77' -> (51.77, 'GBP'). Returns (None, '') if no number is found."""
    raw = clean_text(raw).replace("Â", "")  # stray byte when a page is decoded with the wrong charset
    match = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return None, ""
    amount = float(match.group().replace(",", ""))
    currency = next((code for symbol, code in CURRENCY_SYMBOLS.items() if symbol in raw), "")
    return round(amount, 2), currency


def parse_rating(css_classes):
    """['star-rating', 'Three'] -> 3. Unknown values -> None."""
    for cls in css_classes or []:
        if cls.lower() in RATING_WORDS:
            return RATING_WORDS[cls.lower()]
    return None


def parse_availability(raw):
    """'In stock (22 available)' -> (True, 22); 'In stock' -> (True, None); 'Out of stock' -> (False, 0)."""
    text = clean_text(raw).lower()
    in_stock = "in stock" in text and "out of stock" not in text
    count = re.search(r"(\d+)\s+available", text)
    if count:
        return in_stock, int(count.group(1))
    return in_stock, None if in_stock else 0
