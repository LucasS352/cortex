FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/scripts TZ=America/Sao_Paulo
WORKDIR /app
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt \
    && useradd --system --uid 10001 --home /app cortex \
    && mkdir -p /app/scripts/output /app/Instrucoes_master \
    && chown -R cortex:cortex /app
COPY --chown=cortex:cortex scripts/ /app/scripts/
COPY --chown=cortex:cortex infra/ /app/infra/
USER cortex
EXPOSE 8001 8003
CMD ["uvicorn", "simulator_app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
