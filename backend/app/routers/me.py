from fastapi import APIRouter, HTTPException, status

from app.auth import AuthenticatedUser, CurrentCustomerId, CurrentUser
from app.customers.dependencies import CustomerRepositoryDep
from app.customers.models import CustomerProfile
from app.customers.service import CustomerNotFoundError, get_customer_profile

router = APIRouter()


@router.get("/me", response_model=AuthenticatedUser)
def read_me(user: CurrentUser, _customer_id: CurrentCustomerId) -> AuthenticatedUser:
    """The signed-in user's identity, from their verified Cognito ID token."""
    return user


# Takes no customer_id parameter: the only customer it can return is the caller's own.
@router.get("/me/profile", response_model=CustomerProfile)
def read_my_profile(customer_id: CurrentCustomerId, repository: CustomerRepositoryDep) -> CustomerProfile:
    """The signed-in customer's profile."""
    try:
        return get_customer_profile(repository, customer_id)
    except CustomerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer profile not found") from error
