# Multi-stage Dockerfile for Kiro v3.0 Optimizations
# Stage 1: Build Rust FFI library
# Stage 2: Python runtime with compiled Rust extension

# =============================================================================
# Stage 1: Rust Builder
# =============================================================================
FROM rust:1.78-slim-bookworm AS rust-builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy Rust source
COPY rust/Cargo.toml rust/Cargo.toml
COPY rust/src/ rust/src/

# Build release library
RUN cd rust && cargo build --release

# Strip symbols for smaller binary
RUN strip /build/rust/target/release/libkiro_actor.so || true

# =============================================================================
# Stage 2: Python Runtime
# =============================================================================
FROM python:3.11-slim-bookworm AS python-runtime

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" || \
    pip install --no-cache-dir \
    asyncio \
    aiohttp \
    prometheus-client \
    pytest \
    pytest-asyncio \
    pytest-benchmark \
    black \
    ruff \
    mypy

# Copy Rust compiled library from builder
COPY --from=rust-builder /build/rust/target/release/libkiro_actor.so /app/rust/
COPY --from=rust-builder /build/rust/target/release/libkiro_actor.a /app/rust/

# Copy application code
COPY engine/ /app/engine/
COPY rust/ffi.py /app/rust/
COPY tests/ /app/tests/
COPY examples/ /app/examples/
COPY docs/ /app/docs/

# Set environment
ENV PYTHONPATH=/app
ENV KIRO_RUST_LIB=/app/rust/libkiro_actor.so
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import engine; print('OK')" || exit 1

# Default command
CMD ["python", "-m", "pytest", "tests/", "-v", "--tb=short"]

# =============================================================================
# Stage 3: Production (distroless-like minimal image)
# =============================================================================
FROM python:3.11-slim-bookworm AS production

WORKDIR /app

# Only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-runtime /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-runtime /app /app

ENV PYTHONPATH=/app
ENV KIRO_RUST_LIB=/app/rust/libkiro_actor.so
ENV PYTHONUNBUFFERED=1

USER nobody

CMD ["python", "examples/full_system.py"]

# =============================================================================
# Stage 4: Development (includes all tools)
# =============================================================================
FROM python-runtime AS development

# Install additional dev tools
RUN pip install --no-cache-dir \
    ipython \
    jupyter \
    sphinx \
    mkdocs \
    mkdocs-material

# Mount source for live editing
VOLUME ["/app"]

CMD ["/bin/bash"]