FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# expose the HTTP port for clarity (optional but nice)
EXPOSE 8080

# Run the MCP server in Streamable HTTP mode
CMD ["python", "server.py"]

