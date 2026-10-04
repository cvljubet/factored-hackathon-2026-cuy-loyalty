import { Amplify } from 'aws-amplify'

export interface CognitoConfig {
  region: string
  userPoolId: string
  userPoolClientId: string
}

type CognitoEnv = Pick<ImportMetaEnv, 'VITE_AWS_REGION' | 'VITE_COGNITO_USER_POOL_ID' | 'VITE_COGNITO_CLIENT_ID'>

export function readCognitoConfig(env: CognitoEnv = import.meta.env): CognitoConfig {
  const values = {
    VITE_AWS_REGION: env.VITE_AWS_REGION?.trim(),
    VITE_COGNITO_USER_POOL_ID: env.VITE_COGNITO_USER_POOL_ID?.trim(),
    VITE_COGNITO_CLIENT_ID: env.VITE_COGNITO_CLIENT_ID?.trim(),
  }
  const missing = Object.entries(values)
    .filter(([, value]) => !value)
    .map(([name]) => name)
  if (missing.length > 0) {
    throw new Error(`Missing Cognito configuration: ${missing.join(', ')}. Copy frontend/.env.example to .env.local.`)
  }

  const config = {
    region: values.VITE_AWS_REGION!,
    userPoolId: values.VITE_COGNITO_USER_POOL_ID!,
    userPoolClientId: values.VITE_COGNITO_CLIENT_ID!,
  }
  // Pool IDs are "<region>_<id>"; a mismatch means the env values were mixed up.
  if (!config.userPoolId.startsWith(`${config.region}_`)) {
    throw new Error(`VITE_COGNITO_USER_POOL_ID (${config.userPoolId}) is not in region ${config.region}.`)
  }
  return config
}

export function configureAmplify(config: CognitoConfig = readCognitoConfig()) {
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolClientId,
        loginWith: { email: true },
      },
    },
  })
}
