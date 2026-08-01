import hmac

from fastapi import HTTPException, Request, status


async def require_csrf(request: Request, expected_token: str) -> None:
    submitted = request.headers.get("X-CSRF-Token", "")
    if not submitted:
        form = await request.form()
        submitted = str(form.get("csrf_token", ""))
    if not submitted or not hmac.compare_digest(submitted, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
