# syntax=docker/dockerfile:1.6
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings
WORKDIR /srv/billing

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY manage.py ./
COPY config ./config
COPY apps ./apps

RUN DJANGO_SECRET_KEY=build DJANGO_DEBUG=false python manage.py collectstatic --noinput \
    && addgroup --system billing && adduser --system --ingroup billing billing \
    && chown -R billing:billing /srv/billing
USER billing

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=2).status == 200 else 1)"

# SEED_DEMO_ON_START=true loads the idempotent demo data after migrating (used by the Render
# blueprint, where the free plan has no shell); the admin login and API token appear in the logs.
CMD ["sh", "-c", "python manage.py migrate --noinput && if [ \"$SEED_DEMO_ON_START\" = true ]; then python manage.py seed_demo; fi && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3 --access-logfile -"]
