FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
ARG INSTALL_OCR=false
RUN if [ "$INSTALL_OCR" = true ]; then apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
