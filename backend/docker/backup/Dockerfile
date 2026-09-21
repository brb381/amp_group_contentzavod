FROM postgres:17-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-boto3 restic \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ops/local_backup.py /app/local_backup.py
USER postgres

ENTRYPOINT ["python3", "/app/local_backup.py"]
