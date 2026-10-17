FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Shell form CMD so ${PORT:-8080} expands dynamically in Cloud Run
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
