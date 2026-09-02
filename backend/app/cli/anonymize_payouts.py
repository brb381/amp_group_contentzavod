import argparse
import logging
from datetime import date

from app.audit.service import AuditContext
from app.clock import utc_now
from app.database.config import DatabaseSettings
from app.database.factory import create_session_factory
from app.logging_config import configure_logging
from app.payouts.retention import (
    PAYOUT_PII_RETENTION_YEARS,
    anonymize_eligible_payout_pii,
)
from app.retention.service import anonymize_eligible_account_pii


logger = logging.getLogger(__name__)
SessionLocal = create_session_factory(DatabaseSettings().database_url)


def _retention_cutoff(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def run(*, years: int = PAYOUT_PII_RETENTION_YEARS, batch_size: int = 100) -> int:
    if years < PAYOUT_PII_RETENTION_YEARS:
        raise ValueError("payout PII must be retained for at least five years")
    now = utc_now()
    cutoff = _retention_cutoff(now.date(), years)
    payout_total = 0
    account_total = 0
    while True:
        with SessionLocal.begin() as db:
            payout_result = anonymize_eligible_payout_pii(
                db,
                cutoff=cutoff,
                max_bloggers=batch_size,
                anonymized_at=now,
                audit_context=AuditContext(
                    request_id=f"payout-retention:{now.isoformat()}",
                    ip_address="127.0.0.1",
                    user_agent="payout-retention-job",
                ),
            )
            account_result = anonymize_eligible_account_pii(
                db,
                cutoff=cutoff,
                max_accounts=batch_size,
                anonymized_at=now,
                audit_context=AuditContext(
                    request_id=f"account-retention:{now.isoformat()}",
                    ip_address="127.0.0.1",
                    user_agent="account-retention-job",
                ),
            )
        payout_total += payout_result.bloggers
        account_total += account_result.accounts
        if payout_result.bloggers < batch_size and account_result.accounts < batch_size:
            break
    logger.info(
        "Creator PII retention completed",
        extra={"payout_bloggers": payout_total, "accounts": account_total},
    )
    return account_total


def main() -> None:
    parser = argparse.ArgumentParser(description="Anonymize expired creator PII")
    parser.add_argument("--years", type=int, default=PAYOUT_PII_RETENTION_YEARS)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    configure_logging("creator-retention")
    run(years=args.years, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
