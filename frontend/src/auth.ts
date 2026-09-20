import { InMemoryWebStorage, UserManager, WebStorageStateStore } from 'oidc-client-ts'

const appUrl = `${window.location.origin}${import.meta.env.BASE_URL}`

// Public PKCE client `darkangel-spa` in the Infra Keycloak's `ea` realm
// (created by deploy/keycloak/provision-darkangel-client.sh).
const manager = new UserManager({
  authority:
    import.meta.env.VITE_AUTH_AUTHORITY ?? 'https://keycloak.famillelallier.net/realms/ea',
  client_id: import.meta.env.VITE_AUTH_CLIENT_ID ?? 'darkangel-spa',
  redirect_uri: `${appUrl}auth/callback`,
  post_logout_redirect_uri: appUrl,
  scope: 'openid profile email',
  // Tokens live in memory only, out of reach of anything that reads storage. A
  // reload re-runs the redirect, which Keycloak's SSO cookie answers without a form.
  userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
  // Renews with the refresh token shortly before the access token expires.
  automaticSilentRenew: true,
})

export async function accessToken(): Promise<string | null> {
  const user = await manager.getUser()
  return user && !user.expired ? user.access_token : null
}

/** Sends the browser to Keycloak; it comes back to /auth/callback, then `returnTo`. */
export function signIn(returnTo: string): Promise<void> {
  return manager.signinRedirect({ state: returnTo })
}

/** Finishes the redirect on /auth/callback and returns the in-app path to resume. */
export async function completeSignIn(): Promise<string> {
  const { state } = await manager.signinRedirectCallback()
  return typeof state === 'string' && state.startsWith('/') ? state : '/'
}

export function signOut(): Promise<void> {
  return manager.signoutRedirect()
}
