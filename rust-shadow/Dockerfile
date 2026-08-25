FROM rust:1.85-bookworm AS builder
WORKDIR /source
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
RUN cargo build --locked --release -p algotest-rust-api

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 11020 --home /nonexistent --shell /usr/sbin/nologin rustshadow
RUN mkdir /state && chown rustshadow:rustshadow /state
COPY --from=builder /source/target/release/algotest-rust-api /usr/local/bin/algotest-rust-api
USER rustshadow
EXPOSE 18200
ENTRYPOINT ["/usr/local/bin/algotest-rust-api"]
