# Use Playwright official Python image (latest stable)
FROM mcr.microsoft.com/playwright/python:latest

WORKDIR /app

# install Python deps from requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# copy source
COPY . /app

ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
