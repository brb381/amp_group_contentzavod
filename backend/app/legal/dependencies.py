from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import CurrentUser
from app.auth.models import Role, User
from app.database.session import get_db
from app.errors import APIError
from app.legal.service import missing_required_documents


def require_current_participation_documents(
    user: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
) -> User:
    if user.role != Role.BLOGGER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    missing = missing_required_documents(db, user_id=user.id)
    if missing:
        raise APIError(
            409,
            "LEGAL_ACCEPTANCE_REQUIRED",
            "Current legal documents must be accepted before this action",
            {
                "required_documents": [
                    {
                        "document_id": str(document.id),
                        "document_type": document.document_type.value,
                        "version": document.version,
                    }
                    for document in missing
                ]
            },
        )
    return user


CurrentParticipant = Annotated[User, Depends(require_current_participation_documents)]
