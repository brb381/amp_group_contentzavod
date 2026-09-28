FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1 AS base

RUN addgroup -S -g 1000 minio \
    && adduser -S -D -H -u 1000 -G minio minio \
    && mkdir -p /data /home/minio \
    && chown -R minio:minio /data /home/minio

FROM base AS server

ADD --chmod=0755 --checksum=sha256:eef6581f6509f43ece007a6f2eb4c5e3ce41498c8956e919a7ac7b4b170fa431 \
    https://github.com/minio/minio/releases/download/RELEASE.2025-07-23T15-54-02Z/minio.linux-amd64.RELEASE.2025-07-23T15-54-02Z \
    /usr/local/bin/minio

USER 1000:1000
WORKDIR /home/minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]

FROM base AS client

ADD --chmod=0755 --checksum=sha256:ea4a453be116071ab1ccbd24eb8755bf0579649f41a7b94ab9e68571bb9f4a1e \
    https://github.com/minio/mc/releases/download/RELEASE.2025-07-21T05-28-08Z/mc.linux-amd64.RELEASE.2025-07-21T05-28-08Z \
    /usr/local/bin/mc

USER 1000:1000
WORKDIR /home/minio
ENTRYPOINT ["mc"]
