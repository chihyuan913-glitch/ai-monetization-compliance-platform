FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTO_SHUTDOWN=false \
    NO_AUTO_SHUTDOWN=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8770

CMD ["python", "web_app.py", "--host", "0.0.0.0", "--port", "8770", "--no-browser", "--no-auto-shutdown"]
