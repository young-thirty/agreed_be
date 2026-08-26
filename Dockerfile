FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# git은 저장소 탐색, Tesseract 한국어·영어 데이터는 스캔 문서 OCR에 필요하다.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git tesseract-ocr tesseract-ocr-eng tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY core ./core
COPY infra ./infra
COPY models ./models
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
