# Limitations

Track known limitations, assumptions, risks, and areas requiring further validation here.

## Source data defects

Foreign keys the data dictionary declares but the data doesn't honour. They are marked
`known_broken` in `data/pipelines/glue/dq_rules.py`: `silver/_fk_report/` still shows their orphan
rate (status `known_broken`), but they never fail the pipeline and rows get no orphan flag.

| Column | What the data shows (2026-10-04) | Consequence |
|---|---|---|
| `customers.registration_branch_id` | 150,000 distinct values for 150,000 customers and 350 branches; 99.997% match no branch | A customer's registration branch is unknown |
| `service_agents.assigned_branch_id` | 833 distinct values for 350 branches; 99.8% match no branch | An agent's branch is unknown; routing can't use it |
| `complaints.origin_interaction_id` | Empty in every row (`all_null` in `silver/_rule_report/`, `not_checkable` in `_fk_report`) | A complaint can't be linked to the call that started it |
