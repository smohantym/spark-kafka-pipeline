# Multi-arch python base works fine on M3
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY producer.py .

# Default command can be overridden in docker-compose
CMD ["python", "producer.py"]
