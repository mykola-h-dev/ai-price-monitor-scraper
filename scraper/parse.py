"""Parse books.toscrape.com listing and product pages with BeautifulSoup + lxml."""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .clean import clean_text, parse_availability, parse_price, parse_rating


def parse_listing(html, page_url):
    """Return (products, next_page_url) for one catalogue page."""
    soup = BeautifulSoup(html, "lxml")
    products = [_parse_card(card, page_url) for card in soup.select("article.product_pod")]
    next_link = soup.select_one("li.next > a")
    next_url = urljoin(page_url, next_link["href"]) if next_link else None
    return products, next_url


def _parse_card(card, page_url):
    link = card.select_one("h3 > a")
    price, currency = parse_price(card.select_one(".price_color").get_text())
    in_stock, _ = parse_availability(card.select_one(".availability").get_text())
    return {
        "title": clean_text(link.get("title") or link.get_text()),  # the title attribute is not truncated
        "price": price,
        "currency": currency,
        "rating": parse_rating(card.select_one("p.star-rating").get("class")),
        "in_stock": in_stock,
        "url": urljoin(page_url, link["href"]),
        "image_url": urljoin(page_url, card.select_one("img")["src"]),
    }


def parse_product_details(html):
    """Extra fields only available on the product page: UPC, category, exact stock count."""
    soup = BeautifulSoup(html, "lxml")
    table = {clean_text(row.th.get_text()): clean_text(row.td.get_text())
             for row in soup.select("table.table-striped tr")}
    breadcrumb = soup.select("ul.breadcrumb li a")
    _, stock_count = parse_availability(table.get("Availability", ""))
    return {
        "upc": table.get("UPC", ""),
        "category": clean_text(breadcrumb[-1].get_text()) if len(breadcrumb) >= 3 else "",
        "stock_count": stock_count,
    }
