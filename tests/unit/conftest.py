"""Keeps every unit test away from real AWS and from the developer's own configuration.

Pydantic AI's ALLOW_MODEL_REQUESTS=False blocks model calls, but not plain boto3 calls
such as ApplyGuardrail. Dummy credentials (and no profile or instance metadata) mean
that a boto3 call a test forgot to stub can never be signed with a real identity.

The backend's settings are cleared from the environment too: Settings reads environment
variables even when a test skips .env, so a shell with e.g. BEDROCK_PROFILE=<profile>
would otherwise make tests build a session for a profile that the null AWS config
above does not have. Tests that need a setting pass it explicitly.
"""

import os

from app.config import Settings

for _name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"):
    os.environ.pop(_name, None)
# Settings matches variable names case-insensitively.
_settings_variables = {name.upper() for name in Settings.model_fields}
for _name in [name for name in os.environ if name.upper() in _settings_variables]:
    os.environ.pop(_name)
os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_EC2_METADATA_DISABLED="true",
    AWS_CONFIG_FILE=os.devnull,
    AWS_SHARED_CREDENTIALS_FILE=os.devnull,
)
