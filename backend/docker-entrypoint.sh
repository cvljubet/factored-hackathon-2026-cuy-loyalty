#!/bin/sh
# In ECS the task role lives in the team account, but Bedrock may run in another (model) account.
# When BEDROCK_ROLE_ARN is set, write an AWS profile that assumes that role using the task's own
# credentials, so no keys are ever stored. boto3 refreshes the assumed-role credentials itself.
# Only the backend's Bedrock client uses the profile (BEDROCK_PROFILE); every other AWS call keeps
# the task role, because no [default] profile is written and AWS_PROFILE is never set.
set -eu

if [ -n "${BEDROCK_ROLE_ARN:-}" ]; then
  # The app user has no home directory, so the config lives in /tmp unless told otherwise.
  : "${AWS_CONFIG_FILE:=/tmp/aws/config}"
  export AWS_CONFIG_FILE
  mkdir -p "$(dirname "$AWS_CONFIG_FILE")"
  cat > "$AWS_CONFIG_FILE" <<PROFILE
[profile ${BEDROCK_PROFILE:-bedrock}]
role_arn = ${BEDROCK_ROLE_ARN}
credential_source = EcsContainer
region = ${BEDROCK_REGION:-us-east-2}
PROFILE
fi

exec "$@"
