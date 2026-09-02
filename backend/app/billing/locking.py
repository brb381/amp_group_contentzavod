from sqlalchemy import select

from app.billing.models import BillingControl


def lock_billing_control(db) -> BillingControl:
    control = db.scalar(
        select(BillingControl).where(BillingControl.id == 1).with_for_update()
    )
    if control is None:
        control = BillingControl(id=1)
        db.add(control)
        db.flush()
    return control
