FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps (SQLite already included in slim)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Default command for STDIO mode (good for local runtime on Smithery)
# For HTTP/SSE, you'll swap this to your HTTP entrypoint (see §7).
CMD ["python", "server.py"]

