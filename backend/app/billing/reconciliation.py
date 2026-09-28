from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class ReconciliationResult:
    ledger_mismatches: int
    payout_reserve_mismatches: int

    @property
    def is_consistent(self) -> bool:
        return self.ledger_mismatches == 0 and self.payout_reserve_mismatches == 0


_LEDGER_MISMATCH_COUNT = text(
    """
    WITH ledger_totals AS (
        SELECT
            blogger_id,
            SUM(available_delta_kopecks) AS available_kopecks,
            SUM(reserved_delta_kopecks) AS reserved_kopecks,
            SUM(paid_delta_kopecks) AS paid_kopecks
        FROM balance_ledger
        GROUP BY blogger_id
    ), mismatches AS (
        SELECT balances.blogger_id
        FROM creator_balances AS balances
        LEFT JOIN ledger_totals AS ledger ON ledger.blogger_id = balances.blogger_id
        WHERE balances.available_kopecks != COALESCE(ledger.available_kopecks, 0)
           OR balances.reserved_kopecks != COALESCE(ledger.reserved_kopecks, 0)
           OR balances.paid_kopecks != COALESCE(ledger.paid_kopecks, 0)
        UNION ALL
        SELECT ledger.blogger_id
        FROM ledger_totals AS ledger
        LEFT JOIN creator_balances AS balances ON balances.blogger_id = ledger.blogger_id
        WHERE balances.blogger_id IS NULL
    )
    SELECT COUNT(*) FROM mismatches
    """
)

_PAYOUT_RESERVE_MISMATCH_COUNT = text(
    """
    WITH active_payouts AS (
        SELECT blogger_id, SUM(amount_kopecks) AS amount_kopecks
        FROM payout_requests
        WHERE status IN ('requested', 'under_review', 'approved')
        GROUP BY blogger_id
    ), mismatches AS (
        SELECT balances.blogger_id
        FROM creator_balances AS balances
        LEFT JOIN active_payouts AS payouts ON payouts.blogger_id = balances.blogger_id
        WHERE balances.reserved_kopecks != COALESCE(payouts.amount_kopecks, 0)
        UNION ALL
        SELECT payouts.blogger_id
        FROM active_payouts AS payouts
        LEFT JOIN creator_balances AS balances ON balances.blogger_id = payouts.blogger_id
        WHERE balances.blogger_id IS NULL
    )
    SELECT COUNT(*) FROM mismatches
    """
)


def reconcile_finances(connection: Connection) -> ReconciliationResult:
    """Compare materialized balances with their authoritative records."""
    return ReconciliationResult(
        ledger_mismatches=int(connection.scalar(_LEDGER_MISMATCH_COUNT) or 0),
        payout_reserve_mismatches=int(
            connection.scalar(_PAYOUT_RESERVE_MISMATCH_COUNT) or 0
        ),
    )
