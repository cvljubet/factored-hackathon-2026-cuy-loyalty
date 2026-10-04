export interface AuthUser {
  email: string
  givenName?: string
  familyName?: string
  /** Links the Cognito user to their record in the customer dataset. */
  customerId?: string
}

export function displayName(user: AuthUser): string {
  return user.givenName?.trim() || user.email.split('@')[0]
}

export function initials(user: AuthUser): string {
  const given = user.givenName?.trim()
  const family = user.familyName?.trim()
  if (given && family) return `${given[0]}${family[0]}`.toUpperCase()
  return displayName(user).slice(0, 2).toUpperCase()
}
