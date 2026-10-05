from agents.serving import ServingRepository
from app.customers.models import CustomerProfile

PROFILE_FIELDS = ("first_name", "last_name", "city", "state", "country")


class CustomerNotFoundError(Exception):
    """No customer record exists for the customer_id."""


def get_customer_profile(serving: ServingRepository, customer_id: str) -> CustomerProfile:
    """The minimal profile of the customer, from the same PROFILE record the agent reads, or
    CustomerNotFoundError. Only PROFILE_FIELDS are read, so no other stored field can reach the
    response.

    customer_id must come from the verified identity, never from request input.
    """
    profile = serving.get_profile(customer_id, PROFILE_FIELDS)
    if profile is None:
        raise CustomerNotFoundError(customer_id)
    return CustomerProfile(customer_id=customer_id, **{k: profile.get(k) for k in PROFILE_FIELDS})
