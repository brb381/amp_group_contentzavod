import math
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import utc_now
from app.catalog.models import Brand, Product
from app.catalog.schemas import ProductCreateRequest, ProductUpdateRequest
from app.errors import APIError


CATALOG_EDITOR_ROLES = {Role.MODERATOR, Role.ADMIN}


def _normalized(value: str) -> str:
    return " ".join(value.split()).lower()


def _is_editor(user: User) -> bool:
    return user.status == AccountStatus.ACTIVE and user.role in CATALOG_EDITOR_ROLES


def _require_locked_editor(db: Session, user_id: uuid.UUID) -> User:
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or not _is_editor(user):
        raise APIError(403, "CATALOG_PERMISSION_CHANGED", "Catalog permissions changed; authenticate again")
    return user


def _json_links(payload: ProductCreateRequest | ProductUpdateRequest) -> list[dict] | None:
    if payload.marketplace_links is None:
        return None
    return [link.model_dump(mode="json") for link in payload.marketplace_links]


def _raise_product_conflict(error: IntegrityError) -> None:
    diagnostic = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    message = str(error.orig).lower()
    if constraint_name == "uq_products_brand_normalized_sku" or (
        constraint_name is None and "products.normalized_sku" in message
    ):
        code = "PRODUCT_SKU_ALREADY_EXISTS"
        text = "A product with this SKU already exists for the brand"
    elif constraint_name == "uq_products_brand_normalized_name" or (
        constraint_name is None and "products.normalized_name" in message
    ):
        code = "PRODUCT_NAME_ALREADY_EXISTS"
        text = "A product with this model name already exists for the brand"
    else:
        raise error
    raise APIError(409, code, text) from error


def list_products(
    db: Session,
    *,
    user: User,
    brand: Brand | None,
    search: str | None,
    is_active: bool,
    page: int,
    page_size: int,
) -> tuple[list[Product], int, int]:
    if not is_active and not _is_editor(user):
        raise APIError(403, "HIDDEN_PRODUCTS_FORBIDDEN", "Hidden products are available only to catalog editors")
    filters = [Product.is_active == is_active]
    if brand:
        filters.append(Product.brand == brand)
    if search:
        term = _normalized(search)
        filters.append(
            or_(
                Product.normalized_name.contains(term, autoescape=True),
                Product.normalized_sku.contains(term, autoescape=True),
                func.lower(Product.publication_name).contains(term, autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(Product).where(*filters)) or 0
    products = list(
        db.scalars(
            select(Product)
            .where(*filters)
            .order_by(Product.brand, Product.normalized_name, Product.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return products, total, math.ceil(total / page_size)


def get_product(db: Session, *, user: User, product_id: uuid.UUID) -> Product:
    product = db.get(Product, product_id)
    if not product or (not product.is_active and not _is_editor(user)):
        raise APIError(404, "PRODUCT_NOT_FOUND", "Product was not found")
    return product


def create_product(
    db: Session,
    *,
    actor: User,
    payload: ProductCreateRequest,
    audit_context: AuditContext,
) -> Product:
    locked_actor = _require_locked_editor(db, actor.id)
    product = Product(
        brand=payload.brand,
        model_name=payload.model_name,
        publication_name=payload.publication_name,
        sku=payload.sku,
        normalized_name=_normalized(payload.model_name),
        normalized_sku=_normalized(payload.sku),
        required_hashtags=payload.required_hashtags,
        content_hint=payload.content_hint,
        marketplace_links=_json_links(payload),
        is_active=payload.is_active,
    )
    db.add(product)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        _raise_product_conflict(error)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PRODUCT_CREATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="product",
        object_id=product.id,
        metadata={"brand": product.brand.value, "is_active": product.is_active},
    )
    return product


def update_product(
    db: Session,
    *,
    actor: User,
    product_id: uuid.UUID,
    payload: ProductUpdateRequest,
    audit_context: AuditContext,
) -> Product:
    locked_actor = _require_locked_editor(db, actor.id)
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if not product:
        raise APIError(404, "PRODUCT_NOT_FOUND", "Product was not found")

    values = payload.model_dump(exclude_unset=True, mode="json")
    if "marketplace_links" in values:
        values["marketplace_links"] = _json_links(payload)
    changes = {field: value for field, value in values.items() if getattr(product, field) != value}
    if not changes:
        return product

    was_active = product.is_active
    for field, value in changes.items():
        setattr(product, field, value)
    if "model_name" in changes:
        product.normalized_name = _normalized(product.model_name)
    if "sku" in changes:
        product.normalized_sku = _normalized(product.sku)
    product.updated_at = utc_now()
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        _raise_product_conflict(error)

    if was_active and not product.is_active:
        action = AuditAction.PRODUCT_HIDDEN
    elif not was_active and product.is_active:
        action = AuditAction.PRODUCT_RESTORED
    else:
        action = AuditAction.PRODUCT_UPDATED
    record_event(
        db,
        context=audit_context,
        action=action,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="product",
        object_id=product.id,
        metadata={"changed_fields": sorted(changes)},
    )
    return product
