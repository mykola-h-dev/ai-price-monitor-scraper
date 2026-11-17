"""One-command offline demo: two runs against a local fictional shop.

    python -m scraper.demo

Serves demo_site/day1 on http://127.0.0.1:8020 (stdlib http.server), scrapes it,
switches the server to demo_site/day2 and scrapes again, so the second run shows
new / removed / price / stock changes in output/demo/report.html and an alert in
output/demo/outbox.log. It also asks for /private/ to show the robots.txt skip.
Nothing outside 127.0.0.1 is contacted.
"""

import logging
import shutil
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .main import EXIT_ROBOTS, ROOT, main

SITE = ROOT / "demo_site"
OUT = ROOT / "output" / "demo"


class DaySwitchingHandler(SimpleHTTPRequestHandler):
    """Serves files from server.site_dir, which the demo can switch between days."""

    def __init__(self, request, client_address, server):
        super().__init__(request, client_address, server, directory=str(server.site_dir))

    def log_message(self, fmt, *args):
        logging.getLogger("demo-site").debug(fmt, *args)


def start_site(day="day1", port=8020):
    server = ThreadingHTTPServer(("127.0.0.1", port), DaySwitchingHandler)
    server.site_dir = SITE / day
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_demo(out_dir=OUT, port=8020, delay=0.2):
    if out_dir.exists():
        shutil.rmtree(out_dir)
    server = start_site("day1", port)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    common = ["--url", f"{base}/catalogue/page-1.html", "--pages", "0", "--delay", str(delay),
              "--out", str(out_dir / "products.csv")]
    try:
        print("\n### Day 1: first run (baseline)", file=sys.stderr, flush=True)
        codes = [main(common)]
        server.site_dir = SITE / "day2"
        print("\n### Day 2: the shop changed overnight", file=sys.stderr, flush=True)
        codes.append(main(common))
        print("\n### robots.txt check: /private/ is disallowed", file=sys.stderr, flush=True)
        codes.append(main(["--url", f"{base}/private/index.html", "--fields", "title", "--delay", str(delay),
                          "--out", str(out_dir / "private.csv")]))
    finally:
        server.shutdown()
        server.server_close()
    print(f"\nReport: {out_dir / 'report.html'}\nOutbox: {out_dir / 'outbox.log'}", file=sys.stderr, flush=True)
    return codes


if __name__ == "__main__":
    results = run_demo()
    sys.exit(0 if results == [0, 0, EXIT_ROBOTS] else 1)
