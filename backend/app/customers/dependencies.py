from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.config import get_settings
from app.customers.repository import CustomerRepository, InMemoryCustomerRepository


@lru_cache
def get_customer_repository() -> CustomerRepository:
    """The process-wide repository, chosen by the CUSTOMER_REPOSITORY setting."""
    backend = get_settings().customer_repository
    if backend == "memory":
        from app.customers.fake_data import SYNTHETIC_CUSTOMERS

        return InMemoryCustomerRepository(SYNTHETIC_CUSTOMERS)
    raise ValueError(f"Unsupported CUSTOMER_REPOSITORY: {backend!r}")


CustomerRepositoryDep = Annotated[CustomerRepository, Depends(get_customer_repository)]
