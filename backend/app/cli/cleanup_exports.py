import argparse

from app.database.factory import create_session_factory
from app.exports.config import get_export_cleanup_settings
from app.exports.processor import cleanup_expired_artifacts
from app.exports.storage import S3ArtifactStore
from app.logging_config import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete expired export artifacts")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size must be between 1 and 1000")

    configure_logging("export-cleanup")
    settings = get_export_cleanup_settings()
    deleted = cleanup_expired_artifacts(
        create_session_factory(settings.database_url),
        S3ArtifactStore(settings),
        batch_size=args.batch_size,
    )
    print(f"Deleted export artifacts: {deleted}")


if __name__ == "__main__":
    main()
