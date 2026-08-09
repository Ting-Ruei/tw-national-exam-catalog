FROM python:3.11-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements/review-ui.txt /opt/tw-exam/requirements-review-ui.txt
RUN python3 -m pip install --no-cache-dir -r /opt/tw-exam/requirements-review-ui.txt

WORKDIR /workspace
ENTRYPOINT ["python3"]
CMD ["scripts/serve_question_review_ui.py", "--host", "0.0.0.0", "--port", "8765", "--mobile-port", "8766", "--review-backend", "sql"]
