"""Generate the offline demo site in demo_site/day1 and demo_site/day2.

Two snapshots of a small FICTIONAL catalogue in the books.toscrape.com layout, so the
scraper, robots.txt check and change detection can be shown without touching a real site.
Day 2 vs day 1: one price drop, one price rise, one item out of stock, one new, one removed.

    python scripts/make_demo_site.py
    python -m http.server 8020 --directory demo_site/day1
"""

import pathlib
from html import escape

ROOT = pathlib.Path(__file__).resolve().parent.parent / "demo_site"
ROBOTS = "User-agent: *\nDisallow: /private/\n"

DAY1 = [  # (slug, title, price, rating, in stock)
    ("harbour-lights", "Harbour Lights", 14.99, "Four", True),
    ("the-quiet-orchard", "The Quiet Orchard", 22.50, "Five", True),
    ("maps-of-small-towns", "Maps of Small Towns", 9.75, "Three", True),
    ("salt-and-iron", "Salt and Iron", 31.00, "Two", True),
    ("a-year-of-bread", "A Year of Bread", 18.20, "Four", True),
    ("the-glass-river", "The Glass River", 12.40, "One", False),
    ("northern-gardens", "Northern Gardens", 27.95, "Three", True),
]


def day2():
    items = [list(i) for i in DAY1 if i[0] != "salt-and-iron"]        # removed
    prices = {"harbour-lights": 11.99, "northern-gardens": 29.95}       # drop / rise
    for item in items:
        item[2] = prices.get(item[0], item[2])
        if item[0] == "a-year-of-bread":
            item[4] = False                                             # now out of stock
    items.insert(2, ["winter-kitchen", "Winter Kitchen", 16.00, "Five", True])  # new
    return [tuple(i) for i in items]


def card(slug, title, price, rating, in_stock):
    stock = '<p class="instock availability">In stock</p>' if in_stock else '<p class="availability">Out of stock</p>'
    return f"""<li><article class="product_pod">
  <div class="image_container"><a href="{slug}/index.html"><img src="../media/{slug}.jpg" alt="{escape(title)}"></a></div>
  <p class="star-rating {rating}"></p>
  <h3><a href="{slug}/index.html" title="{escape(title)}">{escape(title)}</a></h3>
  <div class="product_price"><p class="price_color">&pound;{price:.2f}</p>{stock}</div>
</article></li>"""


def page(items, page_no, last):
    nxt = "" if last else f'<ul class="pager"><li class="next"><a href="page-{page_no + 1}.html">next</a></li></ul>'
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Demo Bookshop - page {page_no}</title></head>"
            f"<body><h1>Demo Bookshop (fictional, for the offline demo)</h1><ol class='row'>\n"
            + "\n".join(card(*i) for i in items) + f"\n</ol>{nxt}</body></html>\n")


def write_day(name, items, per_page=4):
    base = ROOT / name
    (base / "catalogue").mkdir(parents=True, exist_ok=True)
    (base / "private").mkdir(exist_ok=True)
    (base / "robots.txt").write_text(ROBOTS)
    (base / "private" / "index.html").write_text("<html><body>Staff only - robots.txt disallows this.</body></html>\n")
    chunks = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    for n, chunk in enumerate(chunks, start=1):
        (base / "catalogue" / f"page-{n}.html").write_text(page(chunk, n, n == len(chunks)), encoding="utf-8")


if __name__ == "__main__":
    write_day("day1", DAY1)
    write_day("day2", day2())
    print("Wrote", ROOT / "day1", "and", ROOT / "day2")
