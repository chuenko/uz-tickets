# Офіційний образ Playwright з передвстановленим Chromium і залежностями.
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway передає PORT через env; за замовчуванням 8080.
ENV PORT=8080
ENV HEADLESS=true

CMD ["python", "-m", "app.main"]
