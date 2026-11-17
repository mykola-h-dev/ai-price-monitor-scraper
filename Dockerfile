FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home scraper && mkdir -p output && chown -R scraper /app/output
USER scraper

# Default: the offline demo (local fictional shop, day 1 -> day 2, change alert, HTML report).
# Real runs: docker compose run --rm scraper python -m scraper.main --pages 2
CMD ["python", "-m", "scraper.demo"]
