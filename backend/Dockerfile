# syntax=docker/dockerfile:1
# Fast app image: code-only layer on top of algo-backend-base.
# Rebuilds in seconds for Python-only changes.
# Deps (Rust + pip) live in algo-backend-base — rebuilt by start.sh only when native/ or requirements.txt changes.
FROM algo-backend-base:latest

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop", "main:app"]
