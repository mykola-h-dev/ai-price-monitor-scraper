"""Change detection between runs, plus the alert that goes out when something changed.

A small JSON snapshot of the last run (key -> title / price / stock) is kept next to the
CSV. Each run is compared with it:
  - new items           (key not in the previous snapshot)
  - removed items       (only reported when the crawl reached the last page, so a
                         `--pages 2` run does not report everything else as removed)
  - price changes       (old -> new, with % change)
  - stock changes       (in stock <-> out of stock)
The alert text is appended to an outbox file. It is posted to Slack only when
SLACK_WEBHOOK_URL is set (off by default); a Slack failure never fails the run.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

import requests

log = logging.getLogger("scraper")

CURRENCY_SIGNS = {"GBP": "£", "USD": "$", "EUR": "€"}


@dataclass
class ChangeSet:
    new: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    price_changed: list = field(default_factory=list)   # {"item", "old", "new", "pct"}
    stock_changed: list = field(default_factory=list)   # {"item", "old", "new"}
    baseline: bool = False        # True on the first run: nothing to compare with
    removed_checked: bool = True  # False when the crawl was partial

    @property
    def total(self):
        return len(self.new) + len(self.removed) + len(self.price_changed) + len(self.stock_changed)

    def keys(self):
        """{key: change kind} for highlighting rows in the report."""
        kinds = {}
        for item in self.new:
            kinds[item["_key"]] = "new"
        for change in self.price_changed:
            kinds[change["item"]["_key"]] = "price-up" if change["new"] > change["old"] else "price-down"
        for change in self.stock_changed:
            kinds.setdefault(change["item"]["_key"], "stock")
        return kinds


# --- snapshot -----------------------------------------------------------
def _pick(row, names, contains=None):
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    if contains:
        for name, value in row.items():
            if contains in name and value not in (None, ""):
                return value
    return None


def snapshot_of(rows, key="url"):
    """{key: {title, price, currency, in_stock, url}} - field names from --fields are mapped too
    (e.g. 'name' -> title, 'sale_price' -> price, 'available' -> in_stock)."""
    snap = {}
    for row in rows:
        k = row.get(key) or row.get("title")
        if not k:
            continue
        link = row.get("url") or next((v for v in row.values() if isinstance(v, str)
                                       and v.startswith(("http://", "https://"))), None)
        snap[str(k)] = {
            "title": _pick(row, ("title", "name", "product", "product_name")) or str(k),
            "price": _pick(row, ("price",), contains="price"),
            "currency": row.get("currency"),
            "in_stock": _pick(row, ("in_stock", "available", "is_available", "stock")),
            "url": link,
        }
    return snap


def load_snapshot(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["items"]
    except (ValueError, KeyError):
        log.warning("Snapshot %s is unreadable; treating this run as the first one", path)
        return None


def save_snapshot(path, rows, key="url", source=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"saved_at": datetime.now().isoformat(timespec="seconds"), "source": source,
            "items": snapshot_of(rows, key)}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


# --- diff ---------------------------------------------------------------
def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value):
    if isinstance(value, bool) or value is None:
        return value
    return str(value).strip().upper() in ("TRUE", "1", "YES")


def detect_changes(previous, rows, key="url", complete=True):
    """Compare a previous snapshot ({key: item}) with this run's rows."""
    current = snapshot_of(rows, key)
    if previous is None:
        return ChangeSet(baseline=True, removed_checked=complete)
    changes = ChangeSet(removed_checked=complete)
    for k, item in current.items():
        item = {**item, "_key": k}
        old = previous.get(k)
        if old is None:
            changes.new.append(item)
            continue
        old_price, new_price = _num(old.get("price")), _num(item.get("price"))
        if old_price is not None and new_price is not None and abs(old_price - new_price) >= 0.005:
            pct = (new_price - old_price) / old_price * 100 if old_price else 0.0
            changes.price_changed.append({"item": item, "old": old_price, "new": new_price, "pct": round(pct, 1)})
        old_stock, new_stock = _bool(old.get("in_stock")), _bool(item.get("in_stock"))
        if old_stock is not None and new_stock is not None and old_stock != new_stock:
            changes.stock_changed.append({"item": item, "old": old_stock, "new": new_stock})
    if complete:
        changes.removed = [{**item, "_key": k} for k, item in previous.items() if k not in current]
    return changes


# --- alert --------------------------------------------------------------
def money(value, currency=""):
    if value is None:
        return "-"
    return f"{CURRENCY_SIGNS.get(currency or '', '')}{value:,.2f}" + ("" if currency in CURRENCY_SIGNS or not currency
                                                                     else f" {currency}")


def alert_text(changes, source):
    lines = [f"Price watch for {source}: {len(changes.new)} new, {len(changes.removed)} removed, "
             f"{len(changes.price_changed)} price change(s), {len(changes.stock_changed)} stock change(s)"]
    for c in changes.price_changed:
        it = c["item"]
        lines.append(f"~ {it.get('title')}: {money(c['old'], it.get('currency'))} -> "
                     f"{money(c['new'], it.get('currency'))} ({c['pct']:+.1f}%)")
    for c in changes.stock_changed:
        lines.append(f"! {c['item'].get('title')}: {'back in stock' if c['new'] else 'now out of stock'}")
    for it in changes.new:
        lines.append(f"+ New: {it.get('title')} ({money(_num(it.get('price')), it.get('currency'))})")
    for it in changes.removed:
        lines.append(f"- Removed: {it.get('title')}")
    return "\n".join(lines)


def send_alert(changes, source, outbox_path, slack_url=None, post=requests.post):
    """Write the alert to the outbox (always) and Slack (only if a webhook URL is configured).

    Returns the alert text, or None when there was nothing to report.
    """
    if changes.baseline or changes.total == 0:
        return None
    text = alert_text(changes, source)
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with outbox_path.open("a", encoding="utf-8") as fh:
        fh.write(f"=== {stamp} | to: price-alerts (outbox) ===\n{text}\n\n")
    slack_url = slack_url if slack_url is not None else os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        try:
            resp = post(slack_url, json={"text": text}, timeout=15)
            if resp.status_code >= 300:
                log.warning("Slack webhook returned HTTP %s; alert kept in %s", resp.status_code, outbox_path)
        except requests.RequestException as exc:
            log.warning("Slack webhook failed (%s); alert kept in %s", type(exc).__name__, outbox_path)
    return text
