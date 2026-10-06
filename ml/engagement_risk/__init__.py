"""Loyalty engagement-risk model: frozen training and batch scoring.

Research, model selection and the independent test evaluation live in
notebooks/engagement_risk_feasibility_v1.ipynb; this package reproduces that frozen logic.

    python -m ml.engagement_risk.train --profile cuy-loyalty
    python -m ml.engagement_risk.score --profile cuy-loyalty --as-of 2026-06-17
"""
