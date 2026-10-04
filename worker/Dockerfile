# Build a partir da RAIZ do repositório (context: .) para incluir shared/.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/shared
WORKDIR /app
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ ./shared/
COPY worker/converter_worker/ ./converter_worker/
CMD ["python", "-m", "converter_worker.main"]
