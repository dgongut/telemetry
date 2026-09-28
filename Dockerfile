FROM python:3.13-alpine

WORKDIR /app

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server /app/server
COPY projects /app/projects
COPY web /app/web

ENV DATA_DIR=/app/data \
    PROJECTS_DIR=/app/projects \
    WEB_DIR=/app/web \
    PYTHONUNBUFFERED=1

VOLUME /app/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

# The rate limiter counts pings per sender. Behind the reverse proxy every
# request comes from the proxy, so the real sender is read from the header the
# proxy adds. Trusting it from anyone is safe because nobody but the proxy can
# reach this container: the port is only published on 127.0.0.1, or on
# 172.17.0.1 for a proxy running in Docker, and neither exists from outside.
CMD ["uvicorn", "--factory", "app:create_app", "--app-dir", "/app/server", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
