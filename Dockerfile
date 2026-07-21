FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY templates/ ./templates/
COPY public/ ./public/

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV FLATMONITOR_CONFIG=/app/config/domains.yaml

# Run the monitoring daemon
CMD ["python", "-m", "app.main"]
