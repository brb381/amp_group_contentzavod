import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_active_roles, require_csrf
from app.auth.models import Role, User
from app.catalog.models import Brand, Product
from app.catalog.schemas import ProductCreateRequest, ProductListResponse, ProductResponse, ProductUpdateRequest
from app.catalog.service import create_product, get_product, list_products, update_product
from app.database.session import get_db


router = APIRouter(prefix="/products", tags=["products"])
CatalogEditor = Annotated[User, Depends(require_active_roles(Role.MODERATOR, Role.ADMIN))]


@router.get("", response_model=ProductListResponse)
def get_products(
    user: CurrentUser,
    brand: Brand | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=255),
    is_active: bool = Query(default=True, alias="isActive"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> ProductListResponse:
    products, total, total_pages = list_products(
        db, user=user, brand=brand, search=search, is_active=is_active, page=page, page_size=page_size
    )
    return ProductListResponse(
        items=products, page=page, page_size=page_size, total_items=total, total_pages=total_pages
    )


@router.get("/{product_id}", response_model=ProductResponse)
def get_product_detail(product_id: uuid.UUID, user: CurrentUser, db: Session = Depends(get_db, scope="function")) -> Product:
    return get_product(db, user=user, product_id=product_id)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def post_product(
    payload: ProductCreateRequest,
    request: Request,
    editor: CatalogEditor,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> Product:
    return create_product(
        db, actor=editor, payload=payload, audit_context=context_from_request(request)
    )


@router.patch("/{product_id}", response_model=ProductResponse)
def patch_product(
    product_id: uuid.UUID,
    payload: ProductUpdateRequest,
    request: Request,
    editor: CatalogEditor,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> Product:
    return update_product(
        db, actor=editor, product_id=product_id, payload=payload, audit_context=context_from_request(request)
    )
