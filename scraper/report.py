"""Self-contained HTML report of the latest run, with changes highlighted."""

from html import escape

from .changes import money

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1c2430;--muted:#5d6b7a;--line:#e3e7ec;
--new:#e7f6ec;--new-b:#2e9d57;--down:#e8f1fd;--down-b:#2f6fd6;--up:#fdf1e4;--up-b:#d9822b;
--rem:#fdeaea;--rem-b:#c94040;--stock:#f3ecfd;--stock-b:#7a4fc9}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
background:var(--bg);color:var(--text)}main{max-width:1100px;margin:0 auto;padding:28px 18px 48px}
h1{font-size:1.55rem;margin:0 0 4px}h2{font-size:1.1rem;margin:28px 0 10px}.muted{color:var(--muted)}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.9rem;color:var(--muted);margin-bottom:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card b{display:block;font-size:1.7rem;line-height:1.1}.card span{color:var(--muted);font-size:.85rem}
.card.new{border-left:5px solid var(--new-b)}.card.rem{border-left:5px solid var(--rem-b)}
.card.price{border-left:5px solid var(--up-b)}.card.stock{border-left:5px solid var(--stock-b)}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{padding:8px 12px;text-align:left;
border-bottom:1px solid var(--line);vertical-align:top}th{background:#fafbfc;font-weight:600;white-space:nowrap}
td.num{text-align:right;white-space:nowrap}tr:last-child td{border-bottom:0}
tr.new td{background:var(--new)}tr.price-down td{background:var(--down)}tr.price-up td{background:var(--up)}
tr.removed td{background:var(--rem)}tr.stock td{background:var(--stock)}
.tag{display:inline-block;font-size:.75rem;font-weight:600;padding:1px 8px;border-radius:999px;color:#fff}
.tag.new{background:var(--new-b)}.tag.price-down{background:var(--down-b)}.tag.price-up{background:var(--up-b)}
.tag.removed{background:var(--rem-b)}.tag.stock{background:var(--stock-b)}
.old{text-decoration:line-through;color:var(--muted);margin-right:6px}
.note{background:#fff8e1;border:1px solid #f0d98a;border-radius:10px;padding:10px 14px;margin:14px 0}
a{color:#2f6fd6}
"""

LABELS = {"new": "NEW", "price-down": "PRICE DOWN", "price-up": "PRICE UP", "removed": "REMOVED", "stock": "STOCK"}


def _tag(kind):
    return f'<span class="tag {kind}">{LABELS[kind]}</span>'


def _cell(value):
    if value is None or value == "":
        return '<span class="muted">-</span>'
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.2f}"
    text = str(value)
    if text.startswith(("http://", "https://")):
        short = text if len(text) < 60 else text[:57] + "..."
        return f'<a href="{escape(text)}">{escape(short)}</a>'
    return escape(text)


def _change_rows(changes):
    out = []
    for c in changes.price_changed:
        it = c["item"]
        kind = "price-up" if c["new"] > c["old"] else "price-down"
        out.append((kind, it, f'<span class="old">{escape(money(c["old"], it.get("currency")))}</span>'
                              f'{escape(money(c["new"], it.get("currency")))} ({c["pct"]:+.1f}%)'))
    for c in changes.stock_changed:
        out.append(("stock", c["item"], "back in stock" if c["new"] else "now out of stock"))
    for it in changes.new:
        out.append(("new", it, escape(money(_float(it.get("price")), it.get("currency")))))
    for it in changes.removed:
        out.append(("removed", it, "no longer listed"))
    return out


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def render_report(rows, changes, columns, meta, key="url"):
    """Return the report as an HTML string. `meta` holds source, mode, run time, warnings..."""
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Scrape report</title><style>{CSS}</style></head><body><main>",
             "<h1>Scrape report</h1>",
             "<div class='meta'>" + "".join(f"<span><b>{escape(k)}:</b> {escape(str(v))}</span>"
                                           for k, v in meta.items() if k != "warnings") + "</div>"]
    for warning in meta.get("warnings", []):
        parts.append(f"<div class='note'>{escape(warning)}</div>")

    if changes.baseline:
        parts.append("<div class='note'>First run: this is the baseline. Changes will be shown from the next run.</div>")
    parts.append("<div class='cards'>"
                 f"<div class='card'><b>{len(rows)}</b><span>items in this run</span></div>"
                 f"<div class='card new'><b>{len(changes.new)}</b><span>new</span></div>"
                 f"<div class='card rem'><b>{len(changes.removed)}</b><span>removed</span></div>"
                 f"<div class='card price'><b>{len(changes.price_changed)}</b><span>price changes</span></div>"
                 f"<div class='card stock'><b>{len(changes.stock_changed)}</b><span>stock changes</span></div>"
                 "</div>")
    if not changes.removed_checked:
        parts.append("<p class='muted'>Removed items are not checked on partial runs (the crawl did not reach "
                     "the last page).</p>")

    change_rows = _change_rows(changes)
    if change_rows:
        parts.append("<h2>Changes since the previous run</h2><div class='wrap'><table>"
                     "<tr><th>Change</th><th>Item</th><th>Detail</th></tr>")
        for kind, item, detail in change_rows:
            title = escape(str(item.get("title") or item.get("_key")))
            link = item.get("url")
            title_html = f'<a href="{escape(link)}">{title}</a>' if link else title
            parts.append(f"<tr class='{kind}'><td>{_tag(kind)}</td><td>{title_html}</td><td>{detail}</td></tr>")
        parts.append("</table></div>")
    elif not changes.baseline:
        parts.append("<h2>Changes since the previous run</h2><p class='muted'>No changes.</p>")

    kinds = changes.keys()
    parts.append(f"<h2>All items ({len(rows)})</h2><div class='wrap'><table><tr>"
                 + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr>")
    for row in rows:
        kind = kinds.get(str(row.get(key) or row.get("title")), "")
        cells = []
        for c in columns:
            value = row.get(c)
            num = isinstance(value, (int, float)) and not isinstance(value, bool)
            cells.append(f"<td{' class=num' if num else ''}>{_cell(value)}</td>")
        parts.append(f"<tr class='{kind}'>" + "".join(cells) + "</tr>")
    parts.append("</table></div></main></body></html>")
    return "\n".join(parts)
