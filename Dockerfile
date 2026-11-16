FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    DUECLOCK_ENV=production DUECLOCK_DATA_DIR=/app/data PORT=10000
WORKDIR /app
RUN useradd --create-home --uid 10001 dueclock
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt
COPY dueclock ./dueclock
RUN mkdir -p /app/data && chown -R dueclock:dueclock /app
USER dueclock
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/v1/health' % os.environ.get('PORT','10000'), timeout=4)"
# DUECLOCK_ENV=production: the server refuses to start on an unsafe configuration (see: python -m dueclock.core.cli preflight).
CMD ["python", "-m", "dueclock.core.cli", "serve", "--host", "0.0.0.0"]
