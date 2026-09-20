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
# TLS: Keycloak serves a certificate from the private Infra CA, which curl does
# not trust out of the box ("unable to get local issuer certificate"). The CA is
# picked up automatically from the Infra checkout next to INFRA_ENV; KC_CACERT
# points at it elsewhere, and KC_INSECURE=true skips verification entirely.
#
# Usage: scripts/provision-keycloak-client.sh
set -euo pipefail
cd "$(dirname "$0")/.."

KC_URL="${KC_URL:-https://keycloak.famillelallier.net}"
REALM="${REALM:-ea}"
CLIENT_ID="${CLIENT_ID:-darkangel-spa}"
APP_URL="${APP_URL:-https://darkangel.infra.famillelallier.net}"
INFRA_ENV="${INFRA_ENV:-../Infra/.env}"
# The Infra CA, as the Infra checkout lays it out next to its .env. README's
# `SSL_CERT_FILE=.../infra-ca.crt make dev-backend` names the same file.
INFRA_CA="$(dirname "$INFRA_ENV")/certs/infra-ca.crt"

die() { printf 'provision-keycloak-client.sh: %b\n' "$*" >&2; exit 1; }

env_value() { # <key>: last value of KEY= in INFRA_ENV, unquoted
  sed -n "s/^$1=//p" "$INFRA_ENV" 2>/dev/null | tail -1 | sed "s/^[\"']//; s/[\"']\$//"
}
KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN:-$(env_value KEYCLOAK_ADMIN)}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-$(env_value KEYCLOAK_ADMIN_PASSWORD)}"
[ -n "$KEYCLOAK_ADMIN" ] && [ -n "$KEYCLOAK_ADMIN_PASSWORD" ] ||
  die "set KEYCLOAK_ADMIN and KEYCLOAK_ADMIN_PASSWORD, or INFRA_ENV to the Infra .env"

# Which CA to verify Keycloak with: an explicit KC_CACERT first, then the Infra
# checkout's own CA, then whatever SSL_CERT_FILE names (README already sets that
# to this same file for `make dev-backend`).
if [ -n "${KC_CACERT:-}" ]; then
  :
elif [ -r "$INFRA_CA" ]; then
  KC_CACERT="$INFRA_CA"
else
  KC_CACERT="${SSL_CERT_FILE:-}"
fi

# Every request goes through this array, so the CA applies to the admin API as
# much as to the token call below.
CURL=(curl -fsS)
if [ "${KC_INSECURE:-}" = "true" ]; then
  KC_CACERT=
  CURL+=(--insecure)
elif [ -n "$KC_CACERT" ]; then
  [ -r "$KC_CACERT" ] || die "KC_CACERT=$KC_CACERT: no such readable file"
  CURL+=(--cacert "$KC_CACERT")
fi

# curl's TLS exit codes (35 handshake, 60 untrusted certificate, 77 unreadable
# CA file), told apart from a wrong password so the advice fits the failure.
tls_hint() {
  if [ -n "$KC_CACERT" ]; then
    die "TLS to $KC_URL failed while verifying against $KC_CACERT.\n\
  Check that file is the CA that signed Keycloak's certificate, or re-run\n\
  with KC_INSECURE=true to skip verification."
  fi
  die "curl does not trust $KC_URL's certificate: it comes from the private\n\
  Infra CA. Point INFRA_ENV at the Infra checkout so $INFRA_CA is\n\
  found, set KC_CACERT=/path/to/Infra/certs/infra-ca.crt, or re-run with\n\
  KC_INSECURE=true to skip verification."
}

rc=0
TOKEN="$("${CURL[@]}" "$KC_URL/realms/master/protocol/openid-connect/token" \
  --data-urlencode grant_type=password --data-urlencode client_id=admin-cli \
  --data-urlencode "username=$KEYCLOAK_ADMIN" \
  --data-urlencode "password=$KEYCLOAK_ADMIN_PASSWORD" | jq -r .access_token)" || rc=$?
case "$rc" in
  0) ;;
  35|60|77) tls_hint ;;
  *) die "could not log in to $KC_URL as $KEYCLOAK_ADMIN (curl exit $rc)" ;;
esac
[ -n "$TOKEN" ] && [ "$TOKEN" != null ] ||
  die "$KC_URL returned no access token for $KEYCLOAK_ADMIN"

api() { # <method> <path> [curl args...]
  local method="$1" path="$2"; shift 2
  "${CURL[@]}" -X "$method" "$KC_URL/admin/realms/$REALM$path" \
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
