#!/bin/sh
set -eu

until mc alias set local "$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; do
    sleep 2
done
mc mb --ignore-existing local/private-exports
mc anonymous set none local/private-exports
mc admin policy create local amp-export-api /config/api-policy.json
mc admin policy create local amp-export-worker /config/worker-policy.json
mc admin user add local "$S3_API_ACCESS_KEY" "$S3_API_SECRET_KEY"
mc admin user add local "$S3_WORKER_ACCESS_KEY" "$S3_WORKER_SECRET_KEY"
mc admin policy attach local amp-export-api --user "$S3_API_ACCESS_KEY"
mc admin policy attach local amp-export-worker --user "$S3_WORKER_ACCESS_KEY"
mc ilm rule add --expire-days 2 local/private-exports || true
