# syntax=docker/dockerfile:1
FROM ghcr.io/pyo3/maturin:latest AS builder
WORKDIR /wheels
COPY certs/sonicwall-dpi-ssl.crt /tmp/sonicwall-dpi-ssl.crt
RUN cat /etc/pki/tls/certs/ca-bundle.crt /tmp/sonicwall-dpi-ssl.crt > /tmp/ca-bundle-with-sonicwall.crt
ENV SSL_CERT_FILE=/tmp/ca-bundle-with-sonicwall.crt \
    REQUESTS_CA_BUNDLE=/tmp/ca-bundle-with-sonicwall.crt \
    PIP_CERT=/tmp/ca-bundle-with-sonicwall.crt \
    CARGO_HTTP_CAINFO=/tmp/ca-bundle-with-sonicwall.crt \
    CARGO_REGISTRIES_CRATES_IO_PROTOCOL=git \
    CARGO_NET_GIT_FETCH_WITH_CLI=true \
    CARGO_BUILD_JOBS=2

RUN git config --global http.sslVerify false

COPY requirements.txt .
COPY native ./native
# Cache Cargo registry (downloaded crates) and build artifacts across rebuilds.
# When only lib.rs changes, crate deps are already compiled — incremental build takes seconds.
RUN --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/root/.cargo/git \
    --mount=type=cache,target=/wheels/native/target \
    maturin build --release --interpreter /opt/python/cp311-cp311/bin/python3 --manifest-path native/Cargo.toml -o /wheels/native

RUN --mount=type=cache,target=/root/.pip-cache \
    pip wheel --no-cache-dir \
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
COPY --from=builder /wheels/native /wheels/native
RUN --mount=type=cache,target=/root/.pip-cache \
    pip install --no-cache-dir \
        --timeout 120 \
        --retries 5 \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        --trusted-host pypi.python.org \
        --find-links /wheels/native \
        --find-links /wheels \
        /wheels/native/*.whl \
        -r requirements.txt

COPY certs/sonicwall-dpi-ssl.crt /usr/local/share/ca-certificates/sonicwall-dpi-ssl.crt
RUN set -eux; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    if apt-get update -qq -o Acquire::https::Verify-Peer=false; then \
        apt-get install -y --no-install-recommends ca-certificates vmtouch; \
        update-ca-certificates; \
        rm -rf /var/lib/apt/lists/*; \
    else \
        printf '#!/usr/bin/env sh\nexit 0\n' > /usr/local/bin/vmtouch; \
        chmod +x /usr/local/bin/vmtouch; \
        echo "apt repositories unavailable; continuing without system package updates"; \
    fi

COPY . .
RUN mkdir -p /data/cleaned_csvs /data/expiryData /data/strikeData /data/Filter

RUN groupadd -r appuser && useradd -r -s /sbin/nologin -g appuser appuser
RUN chown -R appuser:appuser /app /data

ENV MALLOC_ARENA_MAX=2
ENV PYTHONOPTIMIZE=2
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop", "main:app"]
