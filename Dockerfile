# Dockerfile — Playwright image pinned to v1.56.0 to match Playwright python package
FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app

# install Python deps from requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# copy source
COPY . /app

ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
