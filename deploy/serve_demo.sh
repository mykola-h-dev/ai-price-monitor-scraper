#!/bin/sh
# Hosted demo: runs the offline scrape (day 1 -> day 2 with changes) and serves the HTML report.
# Port comes from $PORT (Render, Railway, etc.), default 10000.
set -e
python -m scraper.demo || true
mkdir -p output/demo/site
cp -r demo_site/day2/. output/demo/site/
sed -e 's#http://127.0.0.1:8020/catalogue/[^"]*/index.html#site/catalogue/page-1.html#g' \
    -e 's#http://127.0.0.1:8020/#site/#g' \
    output/demo/report.html > output/demo/index.html
exec python -m http.server "${PORT:-10000}" --bind 0.0.0.0 -d output/demo
