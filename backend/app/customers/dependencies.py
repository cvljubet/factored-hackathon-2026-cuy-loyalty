from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends

from agents.serving import DynamoServingRepository, InMemoryServingRepository, ServingRepository, dynamodb_serving_table
from app.config import Settings, get_settings
from app.customers.models import CustomerProfile
from app.customers.repository import CustomerRepository, InMemoryCustomerRepository


@lru_cache
def get_customer_repository() -> CustomerRepository:
    """The synthetic customers SERVING_BACKEND=memory serves, chosen by CUSTOMER_REPOSITORY."""
    backend = get_settings().customer_repository
    if backend == "memory":
        from app.customers.fake_data import SYNTHETIC_CUSTOMERS

        return InMemoryCustomerRepository(SYNTHETIC_CUSTOMERS)
    raise ValueError(f"Unsupported CUSTOMER_REPOSITORY: {backend!r}")


CustomerRepositoryDep = Annotated[CustomerRepository, Depends(get_customer_repository)]


class RepositoryProfileSource(InMemoryServingRepository):
    """SERVING_BACKEND=memory: profiles from the CustomerRepository (minimal profile only) and no
    other customer or reference data, so those tools find nothing rather than invent it."""

    def __init__(self, repository: CustomerRepository):
        super().__init__()
        self.repository = repository

    def get_profile(self, customer_id: str, fields: Sequence[str] | None = None) -> Mapping[str, Any] | None:
        customer = self.repository.get_customer(customer_id)
        if customer is None:
            return None
        profile = CustomerProfile.from_customer(customer).model_dump(exclude={"customer_id"})
        return {k: v for k, v in profile.items() if not fields or k in fields}


def build_serving(settings: Settings) -> ServingRepository:
    """Customer and reference data for the agent's tools and GET /me/profile. DynamoDB gets its own
    AWS session (SERVING_AWS_PROFILE, or the default chain: the ECS task role), never the Bedrock
    one: the two may be different accounts. Building it makes no request."""
    if settings.serving_backend == "dynamodb":
        table = dynamodb_serving_table(
            settings.serving_table_name, settings.serving_aws_region, settings.serving_aws_profile
        )
        return DynamoServingRepository(table)
    return RepositoryProfileSource(get_customer_repository())


@lru_cache
def get_serving_repository() -> ServingRepository:
    """The process-wide serving repository, shared by the chat agent and GET /me/profile."""
    return build_serving(get_settings())


ServingRepositoryDep = Annotated[ServingRepository, Depends(get_serving_repository)]
