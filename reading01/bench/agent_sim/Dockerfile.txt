# Approximation of the evaluator's pinned image: scientific-python, pdf-text-tool,
# pdf-render-tool, ocr-tool. The real image is not published; this is a guess.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
      fonts-noto-cjk ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pymupdf pdfplumber pillow numpy pandas
RUN mkdir -p /app/work /app/output /input /skills
WORKDIR /app/work
CMD ["sleep", "infinity"]
