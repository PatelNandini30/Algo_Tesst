FROM python:3.11-slim-bookworm AS builder
WORKDIR /wheels

COPY requirements.txt .
RUN pip wheel --no-cache-dir \
        --timeout 120 \
        --retries 5 \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        --trusted-host pypi.python.org \
        -r requirements.txt

FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app

COPY requirements.txt .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir \
        --timeout 120 \
        --retries 5 \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        --trusted-host pypi.python.org \
        --find-links /wheels \
        -r requirements.txt

COPY . .
RUN mkdir -p /data/cleaned_csvs /data/expiryData /data/strikeData /data/Filter

RUN groupadd -r appuser && useradd -r -s /sbin/nologin -g appuser appuser
RUN chown -R appuser:appuser /app /data

ENV MALLOC_ARENA_MAX=2
ENV UVICORN_LOOP=uvloop
ENV UVICORN_WORKERS=2
ENV PYTHONOPTIMIZE=2
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop", "--workers", "2"]
