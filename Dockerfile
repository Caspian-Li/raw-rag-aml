FROM python:3.12-slim-bookworm
ARG AML_BUILD_COMMIT=unrecorded
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken \
    AML_DATABASE=/app/data/memory.sqlite3 AML_BUILD_COMMIT=${AML_BUILD_COMMIT}
WORKDIR /app
COPY requirements.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt \
    && python -c "import tiktoken; assert tiktoken.encoding_for_model('gpt-4').name == 'cl100k_base'" \
    && useradd --uid 10001 --create-home appuser
COPY --chown=appuser:appuser . /app
RUN mkdir -p /app/data && chown appuser:appuser /app/data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8000/health',timeout=3)"
CMD ["python", "-m", "rawrag_aml", "--host", "0.0.0.0", "--port", "8000"]
