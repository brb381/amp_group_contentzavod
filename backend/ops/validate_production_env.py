import argparse
import re
from pathlib import Path
from urllib.parse import urlparse


SECRET_NAMES = (
    "DB_MIGRATION_PASSWORD",
    "DB_API_PASSWORD",
    "DB_SCHEDULER_PASSWORD",
    "DB_EMAIL_WORKER_PASSWORD",
    "DB_YOUTUBE_WORKER_PASSWORD",
    "DB_TIKTOK_WORKER_PASSWORD",
    "DB_VK_WORKER_PASSWORD",
    "DB_RUTUBE_WORKER_PASSWORD",
    "DB_INSTAGRAM_WORKER_PASSWORD",
    "DB_DZEN_WORKER_PASSWORD",
    "DB_CALCULATION_WORKER_PASSWORD",
    "DB_EXPORT_WORKER_PASSWORD",
    "DB_RETENTION_WORKER_PASSWORD",
    "DB_LIFECYCLE_WORKER_PASSWORD",
    "DB_BACKUP_PASSWORD",
    "DB_MONITOR_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "S3_API_SECRET_KEY",
    "S3_WORKER_SECRET_KEY",
    "S3_BACKUP_SECRET_KEY",
    "S3_RESTORE_SECRET_KEY",
)
IMAGE_PATTERN = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
URL_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9._~-]+$")


def load_env_files(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        names_in_file: set[str] = set()
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line or line.startswith("="):
                raise ValueError(f"{path}:{line_number}: expected NAME=value")
            name, value = (part.strip() for part in line.split("=", 1))
            if name in names_in_file:
                raise ValueError(f"{path}:{line_number}: duplicate variable {name}")
            names_in_file.add(name)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[name] = value
    return values


def _require_real_email(values: dict[str, str], name: str) -> None:
    value = values.get(name, "")
    _, separator, domain = value.rpartition("@")
    if not separator or not domain or domain.lower().endswith((".test", ".local")):
        raise ValueError(f"{name} must use a real address")


def validate_environment(values: dict[str, str]) -> None:
    secrets = []
    for name in SECRET_NAMES:
        value = values.get(name, "")
        if len(value) < 24:
            raise ValueError(f"{name} must contain at least 24 characters")
        if not URL_SAFE_PATTERN.fullmatch(value):
            raise ValueError(f"{name} must be URL-safe")
        if value.lower().startswith("replace-"):
            raise ValueError(f"{name} still contains an example value")
        secrets.append(value)
    if len(secrets) != len(set(secrets)):
        raise ValueError("database and storage secrets must not be reused")

    jwt_secret = values.get("JWT_SECRET", "")
    if len(jwt_secret.encode("utf-8")) < 48:
        raise ValueError("JWT_SECRET must contain at least 48 bytes")
    if jwt_secret.lower().startswith(("replace-", "local-development", "test-secret")):
        raise ValueError("JWT_SECRET still contains an example or development value")

    frontend = urlparse(values.get("FRONTEND_URL", ""))
    if frontend.scheme != "https" or not frontend.hostname or frontend.hostname in {
        "localhost",
        "127.0.0.1",
    }:
        raise ValueError("FRONTEND_URL must be a public HTTPS URL")

    for name in ("BACKEND_IMAGE", "WEB_IMAGE"):
        if not IMAGE_PATTERN.fullmatch(values.get(name, "")):
            raise ValueError(f"{name} must use an immutable image@sha256 digest")

    for prefix in ("", "MONITOR_"):
        host = values.get(f"{prefix}SMTP_HOST", "").lower()
        if not host or host in {"mailpit", "localhost", "127.0.0.1"}:
            raise ValueError(f"{prefix}SMTP_HOST must point to a real mail service")
        use_tls = values.get(f"{prefix}SMTP_USE_TLS", "").lower() == "true"
        use_ssl = values.get(f"{prefix}SMTP_USE_SSL", "").lower() == "true"
        if use_tls == use_ssl:
            raise ValueError(
                f"exactly one of {prefix}SMTP_USE_TLS and {prefix}SMTP_USE_SSL must be true"
            )
        _require_real_email(values, f"{prefix}SMTP_FROM_EMAIL")
        username = values.get(f"{prefix}SMTP_USERNAME", "")
        password = values.get(f"{prefix}SMTP_PASSWORD", "")
        if bool(username) != bool(password):
            raise ValueError(
                f"{prefix}SMTP_USERNAME and {prefix}SMTP_PASSWORD must be set together"
            )

    _require_real_email(values, "MONITOR_ALERT_EMAIL")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", action="append", type=Path, required=True)
    args = parser.parse_args()
    validate_environment(load_env_files(args.env_file))
    print("Production environment validation passed.")


if __name__ == "__main__":
    main()
