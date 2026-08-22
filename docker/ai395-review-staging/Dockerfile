FROM python:3.11-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements/ai395-review-staging.txt /opt/ai395/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /opt/ai395/requirements.txt

WORKDIR /workspace
CMD ["python3", "scripts/ai395_review_staging_http.py", "--host", "0.0.0.0", "--port", "8080"]
