from pydantic import BaseModel


class Customer(BaseModel):
    """A customer record as served online; field names match gold customer_360."""

    customer_id: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    mobile_phone: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None


class CustomerProfile(BaseModel):
    """The minimal profile returned to the signed-in customer (no contact details)."""

    customer_id: str
    first_name: str | None
    last_name: str | None
    city: str | None
    state: str | None
    country: str | None

    @classmethod
    def from_customer(cls, customer: Customer) -> "CustomerProfile":
        return cls.model_validate(customer.model_dump(include=set(cls.model_fields)))
