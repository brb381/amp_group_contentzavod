import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.auth.models import User
from app.billing.models import BalanceLedgerEntry, CreatorBalance


class WalletInvariantError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def lock_creator_balance(db: Session, *, blogger_id: uuid.UUID) -> CreatorBalance:
    """Get or create a balance, then hold its row lock until the caller commits."""

    owner_id = db.scalar(
        select(User.id)
        .where(User.id == blogger_id)
        .with_for_update(read=True, key_share=True)
    )
    if owner_id is None:
        raise WalletInvariantError("BALANCE_OWNER_MISSING", "Creator account does not exist")

    balance = db.scalar(
        select(CreatorBalance)
        .where(CreatorBalance.blogger_id == blogger_id)
        .with_for_update()
    )
    if balance is not None:
        return balance

    values = {
        "blogger_id": blogger_id,
        "available_kopecks": 0,
        "reserved_kopecks": 0,
        "paid_kopecks": 0,
    }
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        statement = postgresql_insert(CreatorBalance).values(**values)
        db.execute(statement.on_conflict_do_nothing(index_elements=["blogger_id"]))
    elif dialect_name == "sqlite":
        statement = sqlite_insert(CreatorBalance).values(**values)
        db.execute(statement.on_conflict_do_nothing(index_elements=["blogger_id"]))
    else:
        db.add(CreatorBalance(**values))
        db.flush()

    balance = db.scalar(
        select(CreatorBalance)
        .where(CreatorBalance.blogger_id == blogger_id)
        .with_for_update()
    )
    if balance is None:
        raise WalletInvariantError(
            "BALANCE_CREATION_FAILED", "Creator balance could not be created"
        )
    return balance


def _validate_amount(amount_kopecks: int) -> None:
    if amount_kopecks <= 0:
        raise WalletInvariantError(
            "PAYOUT_AMOUNT_INVALID", "Payout amount must be greater than zero"
        )


def _ledger_matches(
    entry: BalanceLedgerEntry,
    *,
    blogger_id: uuid.UUID,
    operation_type: str,
    available_delta_kopecks: int,
    reserved_delta_kopecks: int,
    paid_delta_kopecks: int,
    payout_request_id: uuid.UUID,
) -> bool:
    return (
        entry.blogger_id == blogger_id
        and getattr(entry.operation_type, "value", entry.operation_type) == operation_type
        and entry.available_delta_kopecks == available_delta_kopecks
        and entry.reserved_delta_kopecks == reserved_delta_kopecks
        and entry.paid_delta_kopecks == paid_delta_kopecks
        and entry.reference_type == "payout_request"
        and entry.reference_id == payout_request_id
    )


def _existing_ledger_or_none(
    db: Session,
    *,
    idempotency_key: str,
    blogger_id: uuid.UUID,
    operation_type: str,
    available_delta_kopecks: int,
    reserved_delta_kopecks: int,
    paid_delta_kopecks: int,
    payout_request_id: uuid.UUID,
) -> BalanceLedgerEntry | None:
    entry = next(
        (
            candidate
            for candidate in db.new
            if isinstance(candidate, BalanceLedgerEntry)
            and candidate.idempotency_key == idempotency_key
        ),
        None,
    )
    if entry is None:
        entry = db.scalar(
            select(BalanceLedgerEntry).where(
                BalanceLedgerEntry.idempotency_key == idempotency_key
            )
        )
    if entry is None:
        return None
    if not _ledger_matches(
        entry,
        blogger_id=blogger_id,
        operation_type=operation_type,
        available_delta_kopecks=available_delta_kopecks,
        reserved_delta_kopecks=reserved_delta_kopecks,
        paid_delta_kopecks=paid_delta_kopecks,
        payout_request_id=payout_request_id,
    ):
        raise WalletInvariantError(
            "PAYOUT_LEDGER_CONFLICT",
            "Payout ledger key is already bound to another financial movement",
        )
    return entry


def _add_ledger_entry(
    db: Session,
    *,
    idempotency_key: str,
    blogger_id: uuid.UUID,
    operation_type: str,
    available_delta_kopecks: int,
    reserved_delta_kopecks: int,
    paid_delta_kopecks: int,
    payout_request_id: uuid.UUID,
) -> None:
    db.add(
        BalanceLedgerEntry(
            blogger_id=blogger_id,
            operation_type=operation_type,
            available_delta_kopecks=available_delta_kopecks,
            reserved_delta_kopecks=reserved_delta_kopecks,
            paid_delta_kopecks=paid_delta_kopecks,
            reference_type="payout_request",
            reference_id=payout_request_id,
            idempotency_key=idempotency_key,
        )
    )


def _require_reservation(
    db: Session,
    *,
    blogger_id: uuid.UUID,
    payout_request_id: uuid.UUID,
    amount_kopecks: int,
) -> None:
    reservation = _existing_ledger_or_none(
        db,
        idempotency_key=f"payout-reserve:{payout_request_id}",
        blogger_id=blogger_id,
        operation_type="payout_reserved",
        available_delta_kopecks=-amount_kopecks,
        reserved_delta_kopecks=amount_kopecks,
        paid_delta_kopecks=0,
        payout_request_id=payout_request_id,
    )
    if reservation is None:
        raise WalletInvariantError(
            "PAYOUT_RESERVATION_MISSING",
            "Payout money was not reserved by this request",
        )


def reserve_payout(
    db: Session,
    *,
    blogger_id: uuid.UUID,
    payout_request_id: uuid.UUID,
    amount_kopecks: int,
) -> CreatorBalance:
    """Atomically move the creator's entire available balance into reserve."""

    _validate_amount(amount_kopecks)
    balance = lock_creator_balance(db, blogger_id=blogger_id)
    idempotency_key = f"payout-reserve:{payout_request_id}"
    if _existing_ledger_or_none(
        db,
        idempotency_key=idempotency_key,
        blogger_id=blogger_id,
        operation_type="payout_reserved",
        available_delta_kopecks=-amount_kopecks,
        reserved_delta_kopecks=amount_kopecks,
        paid_delta_kopecks=0,
        payout_request_id=payout_request_id,
    ):
        return balance
    if balance.claim_expired_at is not None:
        raise WalletInvariantError(
            "BALANCE_CLAIM_EXPIRED", "The balance claim period has expired"
        )
    if balance.available_kopecks <= 0:
        raise WalletInvariantError(
            "PAYOUT_BALANCE_EMPTY", "Creator has no available balance to reserve"
        )
    if balance.available_kopecks != amount_kopecks:
        raise WalletInvariantError(
            "PAYOUT_AMOUNT_CHANGED",
            "Payout amount must equal the entire current available balance",
        )
    if balance.reserved_kopecks != 0:
        raise WalletInvariantError(
            "PAYOUT_RESERVED_BALANCE_CONFLICT",
            "Creator already has reserved payout money",
        )
    balance.available_kopecks -= amount_kopecks
    balance.reserved_kopecks += amount_kopecks
    _add_ledger_entry(
        db,
        idempotency_key=idempotency_key,
        blogger_id=blogger_id,
        operation_type="payout_reserved",
        available_delta_kopecks=-amount_kopecks,
        reserved_delta_kopecks=amount_kopecks,
        paid_delta_kopecks=0,
        payout_request_id=payout_request_id,
    )
    return balance


def release_payout(
    db: Session,
    *,
    blogger_id: uuid.UUID,
    payout_request_id: uuid.UUID,
    amount_kopecks: int,
) -> CreatorBalance:
    """Return a rejected payout reservation to the available balance."""

    _validate_amount(amount_kopecks)
    balance = lock_creator_balance(db, blogger_id=blogger_id)
    idempotency_key = f"payout-release:{payout_request_id}"
    if _existing_ledger_or_none(
        db,
        idempotency_key=idempotency_key,
        blogger_id=blogger_id,
        operation_type="payout_released",
        available_delta_kopecks=amount_kopecks,
        reserved_delta_kopecks=-amount_kopecks,
        paid_delta_kopecks=0,
        payout_request_id=payout_request_id,
    ):
        return balance
    _require_reservation(
        db,
        blogger_id=blogger_id,
        payout_request_id=payout_request_id,
        amount_kopecks=amount_kopecks,
    )
    if _existing_ledger_or_none(
        db,
        idempotency_key=f"payout-paid:{payout_request_id}",
        blogger_id=blogger_id,
        operation_type="payout_paid",
        available_delta_kopecks=0,
        reserved_delta_kopecks=-amount_kopecks,
        paid_delta_kopecks=amount_kopecks,
        payout_request_id=payout_request_id,
    ):
        raise WalletInvariantError(
            "PAYOUT_ALREADY_SETTLED", "A paid payout reservation cannot be released"
        )
    if balance.reserved_kopecks != amount_kopecks:
        raise WalletInvariantError(
            "PAYOUT_RESERVED_BALANCE_CONFLICT",
            "Reserved balance does not equal the payout amount",
        )
    balance.available_kopecks += amount_kopecks
    balance.reserved_kopecks -= amount_kopecks
    _add_ledger_entry(
        db,
        idempotency_key=idempotency_key,
        blogger_id=blogger_id,
        operation_type="payout_released",
        available_delta_kopecks=amount_kopecks,
        reserved_delta_kopecks=-amount_kopecks,
        paid_delta_kopecks=0,
        payout_request_id=payout_request_id,
    )
    return balance


def settle_payout(
    db: Session,
    *,
    blogger_id: uuid.UUID,
    payout_request_id: uuid.UUID,
    amount_kopecks: int,
) -> CreatorBalance:
    """Move an approved reservation to the cumulative paid balance."""

    _validate_amount(amount_kopecks)
    balance = lock_creator_balance(db, blogger_id=blogger_id)
    idempotency_key = f"payout-paid:{payout_request_id}"
    if _existing_ledger_or_none(
        db,
        idempotency_key=idempotency_key,
        blogger_id=blogger_id,
        operation_type="payout_paid",
        available_delta_kopecks=0,
        reserved_delta_kopecks=-amount_kopecks,
        paid_delta_kopecks=amount_kopecks,
        payout_request_id=payout_request_id,
    ):
        return balance
    _require_reservation(
        db,
        blogger_id=blogger_id,
        payout_request_id=payout_request_id,
        amount_kopecks=amount_kopecks,
    )
    if _existing_ledger_or_none(
        db,
        idempotency_key=f"payout-release:{payout_request_id}",
        blogger_id=blogger_id,
        operation_type="payout_released",
        available_delta_kopecks=amount_kopecks,
        reserved_delta_kopecks=-amount_kopecks,
        paid_delta_kopecks=0,
        payout_request_id=payout_request_id,
    ):
        raise WalletInvariantError(
            "PAYOUT_ALREADY_RELEASED", "A released payout reservation cannot be settled"
        )
    if balance.reserved_kopecks != amount_kopecks:
        raise WalletInvariantError(
            "PAYOUT_RESERVED_BALANCE_CONFLICT",
            "Reserved balance does not equal the payout amount",
        )
    balance.reserved_kopecks -= amount_kopecks
    balance.paid_kopecks += amount_kopecks
    _add_ledger_entry(
        db,
        idempotency_key=idempotency_key,
        blogger_id=blogger_id,
        operation_type="payout_paid",
        available_delta_kopecks=0,
        reserved_delta_kopecks=-amount_kopecks,
        paid_delta_kopecks=amount_kopecks,
        payout_request_id=payout_request_id,
    )
    return balance
