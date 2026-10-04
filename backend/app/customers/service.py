from app.customers.models import CustomerProfile
from app.customers.repository import CustomerRepository


class CustomerNotFoundError(Exception):
    """No customer record exists for the customer_id."""


def get_customer_profile(repository: CustomerRepository, customer_id: str) -> CustomerProfile:
    """The minimal profile of the customer, or CustomerNotFoundError.

    customer_id must come from the verified identity, never from request input.
    """
    customer = repository.get_customer(customer_id)
    if customer is None:
        raise CustomerNotFoundError(customer_id)
    return CustomerProfile.from_customer(customer)
