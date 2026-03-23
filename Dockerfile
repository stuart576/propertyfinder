FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Data volume for persistence
VOLUME /data

EXPOSE 8585

CMD ["python", "main.py"]
