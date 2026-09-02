from sqlalchemy import text
from sqlalchemy.orm import Session


DEFAULT_LOCK_TIMEOUT_MS = 5_000
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000


def set_transaction_timeouts(
    db: Session,
    *,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    if lock_timeout_ms <= 0 or statement_timeout_ms <= 0:
        raise ValueError("Database transaction timeouts must be positive")
    db.execute(
        text(
            "SELECT set_config('lock_timeout', :lock_timeout, true), "
            "set_config('statement_timeout', :statement_timeout, true)"
        ),
        {
            "lock_timeout": f"{lock_timeout_ms}ms",
            "statement_timeout": f"{statement_timeout_ms}ms",
        },
    )
