from pathlib import Path

import pytest

from ops.validate_production_env import SECRET_NAMES, load_env_files, validate_environment


def valid_environment() -> dict[str, str]:
    values = {
        name: f"{name.lower().replace('_', '-')}-000000000000000000000"
        for name in SECRET_NAMES
    }
    values.update(
        {
            "JWT_SECRET": "jwt-" + "x" * 60,
            "FRONTEND_URL": "https://content.example.com",
            "BACKEND_IMAGE": "registry.example.com/amp/backend@sha256:" + "a" * 64,
            "WEB_IMAGE": "registry.example.com/amp/web@sha256:" + "b" * 64,
            "MINIO_IMAGE": "registry.example.com/amp/minio@sha256:" + "c" * 64,
            "MINIO_MC_IMAGE": "registry.example.com/amp/minio-mc@sha256:" + "d" * 64,
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USE_TLS": "true",
            "SMTP_USE_SSL": "false",
            "SMTP_FROM_EMAIL": "no-reply@example.com",
            "SMTP_USERNAME": "mailer",
            "SMTP_PASSWORD": "smtp-password",
            "MONITOR_SMTP_HOST": "smtp.example.com",
            "MONITOR_SMTP_USE_TLS": "true",
            "MONITOR_SMTP_USE_SSL": "false",
            "MONITOR_SMTP_FROM_EMAIL": "monitor@example.com",
            "MONITOR_SMTP_USERNAME": "monitor",
            "MONITOR_SMTP_PASSWORD": "monitor-password",
            "MONITOR_ALERT_EMAIL": "ops@example.com",
        }
    )
    return values


def test_valid_production_environment_is_accepted():
    validate_environment(valid_environment())


@pytest.mark.parametrize(
    "name", ("BACKEND_IMAGE", "WEB_IMAGE", "MINIO_IMAGE", "MINIO_MC_IMAGE")
)
def test_production_images_must_use_digest(name):
    values = valid_environment()
    values[name] = "registry.example.com/amp/image:latest"

    with pytest.raises(ValueError, match="immutable image@sha256"):
        validate_environment(values)


def test_env_files_allow_release_override_but_reject_duplicate_in_one_file(tmp_path: Path):
    base = tmp_path / "base.env"
    release = tmp_path / "release.env"
    base.write_text("IMAGE=old\n", encoding="utf-8")
    release.write_text("IMAGE=new\n", encoding="utf-8")
    assert load_env_files([base, release])["IMAGE"] == "new"

    base.write_text("IMAGE=old\nIMAGE=duplicate\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate variable IMAGE"):
        load_env_files([base])
