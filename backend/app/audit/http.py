from fastapi import Request

from app.audit.service import AuditContext


def context_from_request(request: Request) -> AuditContext:
    user_agent = request.headers.get("user-agent")
    return AuditContext(
        request_id=str(getattr(request.state, "request_id", "unknown"))[:128],
        ip_address=(request.client.host if request.client else "unknown")[:45],
        user_agent=user_agent[:512] if user_agent else None,
        session_id=getattr(request.state, "session_id", None),
    )
