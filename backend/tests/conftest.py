import hashlib
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-definitely-long-enough-123")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("RATE_LIMIT_REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_FROM_EMAIL", "no-reply@example.test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import get_db
from app.auth.rate_limit import get_rate_limiter
from app.main import create_app

# Import models before metadata creation.
from app.auth import models as _models  # noqa: F401
from app.creators import models as _creator_models  # noqa: F401
from app.audit import models as _audit_models  # noqa: F401
from app.catalog import models as _catalog_models  # noqa: F401
from app.content import models as _content_models  # noqa: F401
from app.outbox import models as _outbox_models  # noqa: F401
from app.youtube import models as _youtube_models  # noqa: F401
from app.readings import models as _reading_models  # noqa: F401
from app.billing import models as _billing_models  # noqa: F401
from app.payouts import models as _payout_models  # noqa: F401
from app.exports import models as _export_models  # noqa: F401
from app.notifications import models as _notification_models  # noqa: F401
from app.support import models as _support_models  # noqa: F401
from app.lifecycle import models as _lifecycle_models  # noqa: F401
from app.account_deletion import models as _account_deletion_models  # noqa: F401
from app.legal import models as _legal_models  # noqa: F401
from app.notifications.catalog import DEFAULT_NOTIFICATION_TEMPLATES
from app.notifications.models import NotificationChannel, NotificationTemplateVersion
from app.legal.models import LegalDocument, LegalDocumentType


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with test_session.begin() as db:
        for revision, document_type, title, requires_reacceptance in (
            (1, LegalDocumentType.PROGRAM_TERMS, "Program terms", True),
            (1, LegalDocumentType.PERSONAL_DATA_CONSENT, "Personal data consent", True),
            (1, LegalDocumentType.PRIVACY_POLICY, "Privacy policy", False),
        ):
            content = f"# {title}\n\nTest legal document content for {document_type.value}."
            db.add(
                LegalDocument(
                    document_type=document_type,
                    version="2026-08",
                    revision=revision,
                    title=title,
                    content_markdown=content,
                    content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    is_current=True,
                    requires_reacceptance=requires_reacceptance,
                )
            )
        for code, definition in DEFAULT_NOTIFICATION_TEMPLATES.items():
            db.add_all(
                [
                    NotificationTemplateVersion(
                        code=code,
                        channel=NotificationChannel.IN_APP,
                        version=1,
                        title_template=definition["title"],
                        body_template=definition["body"],
                        allowed_variables=definition["variables"],
                    ),
                    NotificationTemplateVersion(
                        code=code,
                        channel=NotificationChannel.EMAIL,
                        version=1,
                        subject_template=definition["title"],
                        body_template=definition["body"],
                        allowed_variables=definition["variables"],
                    ),
                ]
            )
    app = create_app()
    app.state.test_session = test_session

    class FakeRateLimiter:
        def __init__(self):
            self.counts = {}

        def consume(self, key: str, *, limit: int, window_seconds: int) -> int | None:
            self.counts[key] = self.counts.get(key, 0) + 1
            return window_seconds if self.counts[key] > limit else None

        def clear(self, key: str) -> None:
            self.counts.pop(key, None)

    fake_rate_limiter = FakeRateLimiter()

    def override_get_db():
        db = test_session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_rate_limiter] = lambda: fake_rate_limiter
    with TestClient(app) as test_client:
        yield test_client
