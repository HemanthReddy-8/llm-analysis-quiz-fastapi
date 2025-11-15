# Dockerfile - Playwright-ready
FROM python:3.12-slim

# System deps required by Chromium
RUN apt-get update && apt-get install -y \
    wget ca-certificates gnupg libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2 \
    libpangox-1.0-0 libpangoxft-1.0-0 libpango1.0-0 libxss1 libgdk-pixbuf2.0-0 \
    libgtk-3-0 fonts-liberation libwoff1 libwoff2 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy source
COPY . /app

# Install Playwright browsers
RUN playwright install --with-deps

ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
