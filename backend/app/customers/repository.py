from collections.abc import Iterable
from typing import Protocol

from app.customers.models import Customer


class CustomerRepository(Protocol):
    """Looks up customers by customer_id, the online serving layer's key.

    Callers pass only a customer_id taken from a verified identity; implementations
    never decide who may read what.
    """

    def get_customer(self, customer_id: str) -> Customer | None:
        """The customer with this ID, or None if there is none."""
        ...


class InMemoryCustomerRepository:
    """A CustomerRepository over a fixed set of records, for local development and tests."""

    def __init__(self, customers: Iterable[Customer]):
        self._customers = {customer.customer_id: customer for customer in customers}

    def get_customer(self, customer_id: str) -> Customer | None:
        return self._customers.get(customer_id)
