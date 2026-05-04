# syntax=docker/dockerfile:1.6
# ── Build stage ────────────────────────────────────────────────────────────
FROM rust:1.95-slim AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock .
# Pre-cache dependencies
RUN --mount=type=secret,id=ca_bundle,required=true \
    mkdir -p src/bin/migrate && \
    echo "fn main(){}" > src/main.rs && \
    echo "fn main(){}" > src/bin/migrate/main.rs && \
    SSL_CERT_FILE=/run/secrets/ca_bundle \
    CARGO_HTTP_CAINFO=/run/secrets/ca_bundle \
    cargo build --release --locked && rm -rf src
COPY src ./src
RUN --mount=type=secret,id=ca_bundle,required=true \
    touch src/main.rs && \
    SSL_CERT_FILE=/run/secrets/ca_bundle \
    CARGO_HTTP_CAINFO=/run/secrets/ca_bundle \
    cargo build --release

# ── Runtime stage ──────────────────────────────────────────────────────────
FROM debian:bookworm-slim
COPY --from=builder /app/target/release/intraday_server /usr/local/bin/
EXPOSE 8001
ENV RUST_LOG=info
CMD ["intraday_server"]
