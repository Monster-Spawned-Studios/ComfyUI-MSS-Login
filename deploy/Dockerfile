# Multi-stage Dockerfile for ComfyUI with MSS-Login
# Provides Python 3.13, CUDA acceleration, SQLCipher, and Playwright headless Chromium.

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

# Install system dependencies (including libsqlcipher-dev and audio/video tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    libsqlcipher0 \
    libsqlcipher-dev \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    ca-certificates \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.13 \
    python3.13-venv \
    python3.13-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for rapid Python dependency management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app

# Clone ComfyUI core
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /app/ComfyUI

WORKDIR /app/ComfyUI

# Set up Python 3.13 virtual environment with uv
RUN uv venv --python python3.13 /app/ComfyUI/.venv
ENV VIRTUAL_ENV=/app/ComfyUI/.venv \
    PATH="/app/ComfyUI/.venv/bin:$PATH"

# Install ComfyUI dependencies with PyTorch CUDA 12.4
RUN uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 \
    && uv pip install -r requirements.txt

# Copy MSS-Login extension into custom_nodes
COPY . /app/ComfyUI/custom_nodes/ComfyUI-MSS-Login

WORKDIR /app/ComfyUI/custom_nodes/ComfyUI-MSS-Login

# Install MSS-Login dependencies and Playwright Chromium for embedded CPE workflow conversion
RUN uv pip install -e . \
    && playwright install --with-deps chromium || true

WORKDIR /app/ComfyUI

EXPOSE 8188

ENV MSS_LOGIN_DATA_DIR=/data/mss-login \
    CPE_EMBEDDED_CONVERT=true

ENTRYPOINT ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
