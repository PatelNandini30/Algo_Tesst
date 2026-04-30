# ── Build stage ────────────────────────────────────────────────────────────
FROM rust:1.78-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock .
# Pre-cache dependencies
RUN mkdir -p src && echo "fn main(){}" > src/main.rs && cargo build --release --locked && rm -f src/main.rs
COPY src ./src
RUN touch src/main.rs && cargo build --release

# ── Runtime stage ──────────────────────────────────────────────────────────
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y libssl3 ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/intraday_server /usr/local/bin/
EXPOSE 8001
ENV RUST_LOG=info
CMD ["intraday_server"]
