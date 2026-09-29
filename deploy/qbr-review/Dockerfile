FROM python:3.11-slim

# The review server itself imports psycopg only when it is asked to talk to a database
# (`scripts/serve_question_review_ui.py:35`), and this deployment never does. The package is
# installed anyway: leaving it out would make the image *only* able to serve the jsonl backend, so
# a future `--review-backend sql` attempt would fail at import with a message about a missing module
# rather than about the thing that was actually wrong. An image that can run the code it contains
# is cheaper than one that can only run one configuration of it.
COPY requirements/review-ui.txt /opt/tw-exam/requirements-review-ui.txt
RUN python3 -m pip install --no-cache-dir -r /opt/tw-exam/requirements-review-ui.txt

# No user is created and nothing is installed into the workspace: the compose file mounts the
# repository read-only and the queue read-only, so the container has nothing to write except the
# review log, which is its own bind mount. `HOME` is pointed at /tmp so a stray tool that wants a
# home directory does not fail on a read-only root.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /workspace
ENTRYPOINT ["python3"]
