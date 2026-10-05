"""Synthetic customers for the in-memory repository; none of this is real customer data.

CLI-00BPQUST6X8L is the demo Cognito user's customer_id, so the local demo resolves
to a profile; its profile fields are invented like the rest.
"""

from app.customers.models import Customer

SYNTHETIC_CUSTOMERS = [
    Customer(
        customer_id="CLI-00BPQUST6X8L",
        first_name="Valentina",
        last_name="Demo",
        email="valentina.demo@example.com",
        mobile_phone="+51 900 000 001",
        city="Arequipa",
        state="Arequipa",
        country="PE",
    ),
    Customer(
        customer_id="CLI-SYNTH000002",
        first_name="Mateus",
        last_name="Exemplo",
        email="mateus.exemplo@example.com",
        mobile_phone="+55 11 90000 0002",
        city="Campinas",
        state="São Paulo",
        country="BR",
    ),
    Customer(
        customer_id="CLI-SYNTH000003",
        first_name="Lucía",
        last_name="Prueba",
        email="lucia.prueba@example.com",
        mobile_phone="+57 300 000 0003",
        city="Medellín",
        state="Antioquia",
        country="CO",
    ),
]
