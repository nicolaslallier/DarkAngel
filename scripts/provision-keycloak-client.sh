#!/usr/bin/env bash
# Create (or update) the `darkangel-spa` client in the Infra Keycloak's `ea`
# realm, through the admin REST API.
#
# The `ea` realm already exists, and Infra's `start --import-realm` is a no-op
# for an existing realm, so a new client has to be pushed to the live server --
# the same reason Infra carries scripts/provision-ea-obsidian-client.sh. This one
# talks HTTPS to the public hostname instead of `docker compose exec`, so it runs
# from any machine that can reach Keycloak, not only the Docker host.
#
# The client mirrors EA's ea-spa: public, authorization code + PKCE S256, exact
# callback URIs (never a trailing `*`, which would be an open redirect), and an
# audience mapper that stamps `darkangel-api` into access tokens -- the audience
# the backend checks (DARKANGEL_AUTH_AUDIENCE).
#
# Safe to re-run: creates the client if absent, otherwise overwrites its settings
# with the ones below (the audience mapper is only added at creation).
#
# Admin credentials: KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD from the
# environment, else read from the Infra repo's .env (INFRA_ENV, default
# ../Infra/.env). Needs curl and jq.
#
# Usage: scripts/provision-keycloak-client.sh
set -euo pipefail
cd "$(dirname "$0")/.."

KC_URL="${KC_URL:-https://keycloak.famillelallier.net}"
REALM="${REALM:-ea}"
CLIENT_ID="${CLIENT_ID:-darkangel-spa}"
APP_URL="${APP_URL:-https://darkangel.infra.famillelallier.net}"
INFRA_ENV="${INFRA_ENV:-../Infra/.env}"

die() { echo "provision-keycloak-client.sh: $*" >&2; exit 1; }

env_value() { # <key>: last value of KEY= in INFRA_ENV, unquoted
  sed -n "s/^$1=//p" "$INFRA_ENV" 2>/dev/null | tail -1 | sed "s/^[\"']//; s/[\"']\$//"
}
KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN:-$(env_value KEYCLOAK_ADMIN)}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-$(env_value KEYCLOAK_ADMIN_PASSWORD)}"
[ -n "$KEYCLOAK_ADMIN" ] && [ -n "$KEYCLOAK_ADMIN_PASSWORD" ] ||
  die "set KEYCLOAK_ADMIN and KEYCLOAK_ADMIN_PASSWORD, or INFRA_ENV to the Infra .env"

TOKEN="$(curl -fsS "$KC_URL/realms/master/protocol/openid-connect/token" \
  --data-urlencode grant_type=password --data-urlencode client_id=admin-cli \
  --data-urlencode "username=$KEYCLOAK_ADMIN" \
  --data-urlencode "password=$KEYCLOAK_ADMIN_PASSWORD" | jq -r .access_token)" ||
  die "could not log in to $KC_URL as $KEYCLOAK_ADMIN"

api() { # <method> <path> [curl args...]
  local method="$1" path="$2"; shift 2
  curl -fsS -X "$method" "$KC_URL/admin/realms/$REALM$path" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' "$@"
}

# Post-logout URIs end in `/`: the SPA sends origin + BASE_URL, compared exactly.
CLIENT="$(jq -n --arg id "$CLIENT_ID" --arg app "$APP_URL" '
  ["\($app)", "http://localhost:5173", "http://127.0.0.1:5173"] as $origins | {
    clientId: $id,
    name: "DarkAngel — SPA",
    protocol: "openid-connect",
    publicClient: true,
    standardFlowEnabled: true,
    directAccessGrantsEnabled: false,
    implicitFlowEnabled: false,
    serviceAccountsEnabled: false,
    attributes: {
      "pkce.code.challenge.method": "S256",
      "post.logout.redirect.uris": ($origins | map("\(.)/") | join("##"))
    },
    redirectUris: ($origins | map("\(.)/auth/callback")),
    webOrigins: ["+"],
    protocolMappers: [{
      name: "darkangel-api-audience",
      protocol: "openid-connect",
      protocolMapper: "oidc-audience-mapper",
      config: {
        "included.custom.audience": "darkangel-api",
        "id.token.claim": "false",
        "access.token.claim": "true"
      }
    }]
  }')"

UUID="$(api GET "/clients?clientId=$CLIENT_ID" | jq -r '.[0].id // empty')"
if [ -z "$UUID" ]; then
  api POST /clients --data "$CLIENT" >/dev/null
  echo "provision-keycloak-client.sh: created $CLIENT_ID in realm $REALM."
else
  api PUT "/clients/$UUID" --data "$CLIENT" >/dev/null
  echo "provision-keycloak-client.sh: updated $CLIENT_ID in realm $REALM."
fi
