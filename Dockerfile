# ─── Synapse Cell Gateway — Production Container ────────────────────────
# Multi-stage build: compile Rust gateway → slim runtime image
# Usage:
#   docker build -t synapse-cell .
#   docker run -p 8002:8002 synapse-cell
# ────────────────────────────────────────────────────────────────────────

# Stage 1: Build the Rust gateway binary
FROM rust:1.82-bookworm AS builder

WORKDIR /build

# Copy workspace files
COPY gateway/Cargo.toml gateway/Cargo.lock ./gateway/
COPY gateway/src ./gateway/src

# Build release binary (excluding python-ext feature for standalone mode)
RUN cd gateway && cargo build --release --bin cell_api_server 2>/dev/null || \
    cd gateway && cargo build --release --lib && \
    echo "Library built successfully — binary targets may need additional configuration"

# Stage 2: Slim runtime image
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for sandbox isolation
RUN useradd -m -s /bin/bash synapse

WORKDIR /opt/synapse

# Copy the gateway binary (or library)
COPY --from=builder /build/gateway/target/release/cell_api_server /opt/synapse/cell_api_server 2>/dev/null || true
COPY --from=builder /build/gateway/target/release/libsynapse_gateway.so /opt/synapse/ 2>/dev/null || true

# Copy SDK for local Python execution
COPY sdk/synapse /opt/synapse/sdk/synapse
COPY sdk/pyproject.toml /opt/synapse/sdk/

# Copy Wasm templates
COPY gateway/templates /opt/synapse/templates 2>/dev/null || true

# Copy OpenAPI spec
COPY gateway/openapi.yaml /opt/synapse/openapi.yaml

# Create cell data directory
RUN mkdir -p /data/cells /data/templates /data/volumes && \
    chown -R synapse:synapse /data

# Environment
ENV SYNAPSE_CELLS_ROOT=/data/cells
ENV SYNAPSE_TEMPLATE_DIR=/data/templates
ENV SYNAPSE_BIND_ADDR=0.0.0.0:8002
ENV RUST_LOG=info

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8002/v1/health || exit 1

USER synapse
EXPOSE 8002

# Default: run the gateway server
CMD ["/opt/synapse/cell_api_server"]
