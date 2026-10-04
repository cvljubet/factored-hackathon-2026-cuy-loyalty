"""Data quality rules the Glue jobs apply, kept as data in one place.

Deployed next to the job scripts and put on their Python path with --extra-py-files.
"""
from typing import NamedTuple


class ForeignKey(NamedTuple):
    child: str
    column: str
    parent: str
    parent_column: str
    nullable: bool = False  # the dictionary says "FK" without NOT NULL: a null is not an orphan


# The 24 relationships in the data dictionary's "Foreign Key Relationships" section,
# grouped by parent as listed there. Nullability comes from each child column's constraints.
FOREIGN_KEYS = [
    ForeignKey("products", "customer_id", "customers", "customer_id"),
    ForeignKey("transactions", "customer_id", "customers", "customer_id"),
    ForeignKey("call_center_interactions", "customer_id", "customers", "customer_id"),
    ForeignKey("call_transcripts", "customer_id", "customers", "customer_id"),
    ForeignKey("satisfaction_surveys", "customer_id", "customers", "customer_id"),
    ForeignKey("digital_events", "customer_id", "customers", "customer_id", nullable=True),
    ForeignKey("complaints", "customer_id", "customers", "customer_id"),
    ForeignKey("campaign_sends", "customer_id", "customers", "customer_id"),

    ForeignKey("customers", "registration_branch_id", "branches", "branch_id"),
    ForeignKey("products", "opening_branch_id", "branches", "branch_id"),
    ForeignKey("service_agents", "assigned_branch_id", "branches", "branch_id", nullable=True),
    ForeignKey("transactions", "branch_id", "branches", "branch_id", nullable=True),
    ForeignKey("complaints", "related_branch_id", "branches", "branch_id", nullable=True),

    ForeignKey("call_center_interactions", "agent_id", "service_agents", "agent_id", nullable=True),
    ForeignKey("call_transcripts", "agent_id", "service_agents", "agent_id"),
    ForeignKey("satisfaction_surveys", "agent_id", "service_agents", "agent_id", nullable=True),
    ForeignKey("complaints", "assigned_agent_id", "service_agents", "agent_id", nullable=True),

    ForeignKey("transactions", "product_id", "products", "product_id"),
    ForeignKey("digital_events", "product_id", "products", "product_id", nullable=True),
    ForeignKey("complaints", "affected_product_id", "products", "product_id", nullable=True),

    ForeignKey("campaign_sends", "campaign_id", "marketing_campaigns", "campaign_id"),

    ForeignKey("call_transcripts", "interaction_id", "call_center_interactions", "interaction_id"),
    ForeignKey("satisfaction_surveys", "interaction_id", "call_center_interactions", "interaction_id", nullable=True),
    # 100% blank in the source, so the report marks it not_checkable instead of passing it.
    ForeignKey("complaints", "origin_interaction_id", "call_center_interactions", "interaction_id", nullable=True),
]

# Orphans as a % of the references that can be checked: non-null values, plus nulls where
# the column is NOT NULL. Above WARN the run logs it; above FAIL the silver job fails.
ORPHAN_WARN_PCT = 1.0
ORPHAN_FAIL_PCT = 5.0
