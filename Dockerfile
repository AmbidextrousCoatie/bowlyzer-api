FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bowlyzerapi ./bowlyzerapi
COPY scripts ./scripts

ENV PARQUET_DIR=/data/parquet
ENV WAREHOUSE_PATH=/var/lib/bowlyzer/bowlyzer.duckdb
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "-m", "bowlyzerapi.server"]
