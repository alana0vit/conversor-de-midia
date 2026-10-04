# Build a partir da RAIZ do repositório (context: .) para incluir shared/.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/shared
WORKDIR /app
COPY notifier/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ ./shared/
COPY notifier/notifier_service/ ./notifier_service/
CMD ["python", "-m", "notifier_service.main"]
