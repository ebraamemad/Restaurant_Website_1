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

# --forwarded-allow-ips: من غيرها، uvicorn بيشوف كل الطلبات جاية من
# 127.0.0.1 وميقدرش يميّز عميل عن التاني في اللوجات ولا في rate limiting.
# الافتراضي 127.0.0.1 ينفع لو مفيش proxy، أو لو في proxy على نفس الـhost.
# غيّره من متغير البيئة لما يكون في proxy بعيد.
CMD ["sh", "-c", "python -m alembic upgrade head && python -m app.seed_admin && exec gunicorn -k uvicorn.workers.UvicornWorker --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1} --bind 0.0.0.0:8000 app.main:app"]
