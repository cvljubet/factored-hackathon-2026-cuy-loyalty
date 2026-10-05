"""Keeps every unit test away from real AWS.

Pydantic AI's ALLOW_MODEL_REQUESTS=False blocks model calls, but not plain boto3 calls
such as ApplyGuardrail. Dummy credentials (and no profile or instance metadata) mean
that a boto3 call a test forgot to stub can never be signed with a real identity.
"""

import os

for _name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"):
    os.environ.pop(_name, None)
os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_EC2_METADATA_DISABLED="true",
    AWS_CONFIG_FILE=os.devnull,
    AWS_SHARED_CREDENTIALS_FILE=os.devnull,
)
