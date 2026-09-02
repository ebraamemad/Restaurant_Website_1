FROM python:3.13.5-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY .env.example ./.env.example

RUN mkdir -p /app/app/uploads

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic upgrade head && python -m app.seed_admin && exec gunicorn -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 app.main:app"]
