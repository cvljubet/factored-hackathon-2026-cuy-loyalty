from fastapi import APIRouter, HTTPException, status

from app.auth import AuthenticatedUser, CurrentUser

router = APIRouter()


@router.get("/me", response_model=AuthenticatedUser)
def read_me(user: CurrentUser) -> AuthenticatedUser:
    """The signed-in user's identity, from their verified Cognito ID token."""
    # Authenticated, but not linked to a customer record, so there is nothing to serve.
    if not user.customer_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is not linked to a customer")
    return user
