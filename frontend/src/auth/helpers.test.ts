import { describe, expect, it } from 'vitest'
import { namedError } from '../test/fakeAuthService'
import { readCognitoConfig } from './config'
import { AuthChallengeError, loginErrorKey } from './errors'
import { displayName, initials } from './user'

describe('displayName / initials', () => {
  it('uses given and family names when present', () => {
    const user = { email: 'cl@example.com', givenName: 'Constanza', familyName: 'Ljubetic' }
    expect(displayName(user)).toBe('Constanza')
    expect(initials(user)).toBe('CL')
  })

  it('falls back to the email local part', () => {
    const user = { email: 'maria.silva@example.com' }
    expect(displayName(user)).toBe('maria.silva')
    expect(initials(user)).toBe('MA')
  })
})

describe('loginErrorKey', () => {
  it.each([
    ['NotAuthorizedException', 'login.errors.invalidCredentials'],
    ['UserNotFoundException', 'login.errors.invalidCredentials'],
    ['UserNotConfirmedException', 'login.errors.userNotConfirmed'],
    ['PasswordResetRequiredException', 'login.errors.passwordResetRequired'],
    ['TooManyRequestsException', 'login.errors.tooManyAttempts'],
    ['NetworkError', 'login.errors.network'],
    ['SomethingElse', 'login.errors.generic'],
  ])('maps %s to %s', (name, key) => {
    expect(loginErrorKey(namedError(name))).toBe(key)
  })

  it('maps the new-password challenge', () => {
    expect(loginErrorKey(new AuthChallengeError('CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED'))).toBe(
      'login.errors.newPasswordRequired',
    )
  })

  it('handles non-Error values', () => {
    expect(loginErrorKey('boom')).toBe('login.errors.generic')
  })
})

describe('readCognitoConfig', () => {
  const valid = {
    VITE_AWS_REGION: 'us-east-2',
    VITE_COGNITO_USER_POOL_ID: 'us-east-2_abc123',
    VITE_COGNITO_CLIENT_ID: 'client123',
  }

  it('reads the Vite env values', () => {
    expect(readCognitoConfig(valid)).toEqual({
      region: 'us-east-2',
      userPoolId: 'us-east-2_abc123',
      userPoolClientId: 'client123',
    })
  })

  it('names every missing variable', () => {
    expect(() => readCognitoConfig({ VITE_AWS_REGION: 'us-east-2' })).toThrow(
      /VITE_COGNITO_USER_POOL_ID, VITE_COGNITO_CLIENT_ID/,
    )
  })

  it('rejects a pool ID from another region', () => {
    expect(() => readCognitoConfig({ ...valid, VITE_AWS_REGION: 'us-east-1' })).toThrow(/not in region us-east-1/)
  })
})
