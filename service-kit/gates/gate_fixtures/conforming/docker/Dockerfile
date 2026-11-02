# A conforming image, written differently from the template's own.
FROM python:3.12-slim@sha256:0000000000000000000000000000000000000000000000000000000000000000
COPY requirements/service.lock requirements/service.lock
RUN pip install --require-hashes -r requirements/service.lock
ENTRYPOINT ["/srv/entrypoint.sh"]
