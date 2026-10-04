/** Cognito asked for another step (e.g. a new password) that this app does not handle yet. */
export class AuthChallengeError extends Error {
  readonly step: string

  constructor(step: string) {
    super(`Unsupported sign-in step: ${step}`)
    this.name = 'AuthChallengeError'
    this.step = step
  }
}

export type LoginErrorKey =
  | 'login.errors.invalidCredentials'
  | 'login.errors.userNotConfirmed'
  | 'login.errors.passwordResetRequired'
  | 'login.errors.newPasswordRequired'
  | 'login.errors.tooManyAttempts'
  | 'login.errors.network'
  | 'login.errors.generic'

/** Maps an error from sign-in to the translation key shown on the login form. */
export function loginErrorKey(error: unknown): LoginErrorKey {
  if (error instanceof AuthChallengeError) {
    return error.step === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED'
      ? 'login.errors.newPasswordRequired'
      : 'login.errors.generic'
  }

  const name = error instanceof Error ? error.name : undefined
  switch (name) {
    case 'NotAuthorizedException':
    case 'UserNotFoundException':
      return 'login.errors.invalidCredentials'
    case 'UserNotConfirmedException':
      return 'login.errors.userNotConfirmed'
    case 'PasswordResetRequiredException':
      return 'login.errors.passwordResetRequired'
    case 'LimitExceededException':
    case 'TooManyRequestsException':
      return 'login.errors.tooManyAttempts'
    case 'NetworkError':
      return 'login.errors.network'
    default:
      return 'login.errors.generic'
  }
}
