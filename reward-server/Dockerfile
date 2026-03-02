# --- Stage 1: Get the Go Compiler ---
# We use the official Go image to extract the toolchain
FROM golang:1.24-bookworm AS go-builder

# --- Stage 2: Final Runtime Image ---
# Using Python 3.12 as the base for our FastAPI reward server
FROM python:3.12-slim-bookworm

# 1. Install necessary system dependencies
# build-essential is required for certain Python C-extensions and Go's race detector
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    bash \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2. Copy Go toolchain from the first stage
COPY --from=go-builder /usr/local/go/ /usr/local/go/
ENV PATH="/usr/local/go/bin:${PATH}"

# 3. Configure Go Environment for containerized execution
# This prevents "permission denied" errors when running in K8s
ENV GOCACHE=/tmp/go-cache
ENV GOPATH=/tmp/go-path
RUN mkdir -p /tmp/go-cache /tmp/go-path && chmod -R 777 /tmp

# 4. Set up the working directory
WORKDIR /app

# 5. Install Python dependencies
# We use a simple requirements file. If you don't have one, 
# you can just RUN pip install fastapi uvicorn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy the Reward Server source code
COPY main.py .

# 7. Expose the API port
EXPOSE 8000

# 8. Start the Reward Server
# We use uvicorn directly to ensure logs are streamed to stdout/stderr
CMD ["python", "main.py"]