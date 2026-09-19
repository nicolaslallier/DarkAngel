#!/usr/bin/env bash
# Drive the "darkangel" stack through Portainer's API, the same way
# Infra/scripts/portainer-stack.sh drives "infra".
#
# Portainer deploys deploy/portainer-stack.yml straight from GitHub and pulls
# the GHCR images on the Docker host, using the registry credentials stored in
# Portainer. Nothing is pulled by the machine running this script, so a broken
# local credential helper (Docker Desktop's "A specified logon session does not
# exist") cannot get in the way.
#
# Usage: scripts/portainer-stack.sh up|pull|down|delete|webhook|selftest
#   up       create the stack, or redeploy it (re-pulling the images)
#   pull     redeploy an existing stack, re-pulling the images
#   down     stop the stack (containers removed, images kept)
#   delete   remove the stack from Portainer
#   webhook  print the stack's redeploy webhook, creating one if needed
#   selftest check the pure helpers without touching Portainer
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=darkangel
REPO_URL=https://github.com/nicolaslallier/DarkAngel
COMPOSE_FILE=deploy/portainer-stack.yml
CURL_IMAGE=curlimages/curl:8.5.0

# Branch Portainer deploys from. The stack definition comes from GitHub, not
# from this checkout, so what is committed on this ref is what runs.
REF="${PORTAINER_REF:-refs/heads/main}"

# Where the curl container reaches Portainer. The default matches the Infra
# stack: Portainer on infra-net, addressed by its service name, so deploying
# never depends on nginx or DNS.
PORTAINER_URL="${PORTAINER_URL:-https://portainer:9443}"
PORTAINER_NETWORK="${PORTAINER_NETWORK:-infra-net}"

# Public Portainer origin, used only to print a webhook URL that GitHub
# Actions can reach. The in-cluster URL above is not routable from a runner.
PORTAINER_PUBLIC_URL="${PORTAINER_PUBLIC_URL:-https://portainer.infra.famillelallier.net}"

die() { printf 'portainer-stack.sh: %b\n' "$*" >&2; exit 1; }
note() { printf 'portainer-stack.sh: %b\n' "$*"; }

# The stack's environment variables, as Portainer's [{name,value}] shape.
# deploy/portainer-stack.yml reads exactly these two; every other variable in
# the environment stays out, so nothing unrelated leaks into the stack.
stack_env() { # <image-owner> <image-tag>
  jq -n --arg owner "$1" --arg tag "$2" \
    '[{name: "IMAGE_OWNER", value: $owner},
      {name: "IMAGE_TAG", value: $tag}]'
}

gen_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  elif [ -r /proc/sys/kernel/random/uuid ]; then
    cat /proc/sys/kernel/random/uuid
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import uuid; print(uuid.uuid4())'
  else
    die "need uuidgen, /proc/sys/kernel/random/uuid or python3 to mint a webhook id"
  fi
}

selftest() {
  local got want
  got="$(stack_env nicolaslallier latest | jq -c .)"
  want='[{"name":"IMAGE_OWNER","value":"nicolaslallier"},{"name":"IMAGE_TAG","value":"latest"}]'
  [ "$got" = "$want" ] || die "selftest: stack_env\n  got:  $got\n  want: $want"
  got="$(stack_env 'o w' 'sha-1234' | jq -r '.[1].value')"
  [ "$got" = sha-1234 ] || die "selftest: stack_env did not carry the tag through"
  case "$(gen_uuid)" in
    [0-9a-f]*-*-*-*-*) ;;
    *) die "selftest: gen_uuid did not produce a uuid" ;;
  esac
  note "selftest ok"
}

# Runs curl in a throwaway container on Portainer's network. The API key
# reaches curl via -K (a config file written inside the container from stdin's
# first line, the body following it), never as a command-line argument, so it
# never shows up in a process list. Not `-e PORTAINER_API_KEY`: whether the
# docker CLI forwards its environment depends on the shell (from WSL, a
# Windows docker.exe does not see it), and a missing key would only surface as
# Portainer's "A valid authorization token is missing".
api() { # <method> <path> [json-body]
  # MSYS_NO_PATHCONV/MSYS2_ARG_CONV_EXCL: under Git for Windows the MSYS
  # runtime rewrites arguments that look like POSIX paths before handing them
  # to the native docker.exe, so "/endpoints" arrives as
  # "C:/Program Files/Git/endpoints" and curl rejects the URL. No-ops on macOS
  # and Linux.
  printf '%s\n%s' "$PORTAINER_API_KEY" "${3:-}" | MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
    docker run --rm -i --network "$PORTAINER_NETWORK" \
    --entrypoint sh "$CURL_IMAGE" -c '
      IFS= read -r key
      printf "header = \"X-API-Key: %s\"\n" "$key" >/tmp/curl.cfg
      out="$(curl -sSk -K /tmp/curl.cfg --fail-with-body -X "$1" \
        -H "Content-Type: application/json" \
        --data-binary @- "$3/api$2" 2>&1)" \
        || { printf "%s\n" "$out" >&2; exit 1; }
      printf "%s" "$out"' sh "$1" "$2" "$PORTAINER_URL" \
    || die "$1 $2 failed (is Portainer up, and is this host on $PORTAINER_NETWORK?)"
}

webhook_url() { printf '%s/api/stacks/webhooks/%s\n' "${PORTAINER_PUBLIC_URL%/}" "$1"; }

cmd="${1:-}"
case "$cmd" in
  selftest) selftest; exit 0 ;;
  up|pull|down|delete|webhook) ;;
  *) die "usage: scripts/portainer-stack.sh up|pull|down|delete|webhook|selftest" ;;
esac

command -v jq >/dev/null || die "missing: jq"
command -v docker >/dev/null || die "missing: docker"

# PORTAINER_API_KEY lives in .portainer.env (gitignored). It is a
# Docker-daemon-root token, so it is kept out of anything handed to a
# container and out of the repository.
if [ -z "${PORTAINER_API_KEY:-}" ] && [ -f .portainer.env ]; then
  set -a; . ./.portainer.env; set +a
fi
if [ -z "${PORTAINER_API_KEY:-}" ] || [ "$PORTAINER_API_KEY" = change-me ]; then
  die "set PORTAINER_API_KEY in .portainer.env (Portainer -> My account -> Access tokens); see README 'Running the stack'"
fi

IMAGE_OWNER="${IMAGE_OWNER:-nicolaslallier}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

eid="${PORTAINER_ENDPOINT_ID:-$(api GET /endpoints | jq -r '[.[] | select(.Type == 1)][0].Id // empty')}"
[ -n "$eid" ] || die "no local Docker environment found in Portainer"
stack="$(api GET /stacks | jq -c --arg n "$STACK" 'first(.[] | select(.Name == $n)) // empty')"
sid=""
[ -z "$stack" ] || sid="$(jq -r .Id <<<"$stack")"

case "$cmd" in
  up|pull)
    env="$(stack_env "$IMAGE_OWNER" "$IMAGE_TAG")"
    if [ -z "$sid" ]; then
      [ "$cmd" = up ] || die "stack '$STACK' does not exist yet -- 'make up' first"
      hook="$(gen_uuid)"
      body="$(jq -n --arg name "$STACK" --arg url "$REPO_URL" --arg ref "$REF" \
        --arg file "$COMPOSE_FILE" --arg hook "$hook" --argjson env "$env" \
        '{Name: $name, RepositoryURL: $url, RepositoryReferenceName: $ref,
          ComposeFile: $file, RepositoryAuthentication: false, Env: $env,
          AutoUpdate: {Webhook: $hook, ForcePullImage: true}}')"
      api POST "/stacks/create/standalone/repository?endpointId=$eid" "$body" >/dev/null
      note "created stack '$STACK' from $REPO_URL ($REF)"
      note "webhook: $(webhook_url "$hook")\n  save it as the PORTAINER_WEBHOOK_URL repository secret so deploy.yml can redeploy"
    else
      # Status 2 is a stopped stack; redeploying one that is down is a no-op
      # unless it is started first.
      if [ "$(jq -r .Status <<<"$stack")" = 2 ]; then
        api POST "/stacks/$sid/start?endpointId=$eid" >/dev/null
      fi
      # PullImage and RepullImageAndRedeploy are the same knob under two
      # names across Portainer versions; sending both means the images are
      # re-pulled whichever one this Portainer reads (it ignores the other).
      body="$(jq -n --arg ref "$REF" --argjson env "$env" \
        '{RepositoryReferenceName: $ref, RepositoryAuthentication: false, Env: $env,
          Prune: false, PullImage: true, RepullImageAndRedeploy: true}')"
      api PUT "/stacks/$sid/git/redeploy?endpointId=$eid" "$body" >/dev/null
      note "redeployed stack '$STACK' ($REF, $IMAGE_OWNER/darkangel-*:$IMAGE_TAG)"
    fi
    ;;
  down)
    [ -n "$sid" ] || die "stack '$STACK' does not exist in Portainer"
    if [ "$(jq -r .Status <<<"$stack")" = 2 ]; then
      note "stack '$STACK' is already stopped"
      exit 0
    fi
    api POST "/stacks/$sid/stop?endpointId=$eid" >/dev/null
    note "stopped stack '$STACK'"
    ;;
  delete)
    [ -n "$sid" ] || { note "no stack '$STACK' in Portainer"; exit 0; }
    api DELETE "/stacks/$sid?endpointId=$eid" >/dev/null
    note "deleted stack '$STACK' from Portainer"
    ;;
  webhook)
    [ -n "$sid" ] || die "stack '$STACK' does not exist in Portainer -- 'make up' first"
    hook="$(jq -r '.AutoUpdate.Webhook // empty' <<<"$stack")"
    if [ -z "$hook" ]; then
      hook="$(gen_uuid)"
      body="$(jq -n --arg ref "$REF" --arg hook "$hook" \
        '{RepositoryReferenceName: $ref, RepositoryAuthentication: false,
          AutoUpdate: {Webhook: $hook, ForcePullImage: true}}')"
      api PUT "/stacks/$sid/git?endpointId=$eid" "$body" >/dev/null
      note "created a redeploy webhook for stack '$STACK'"
    fi
    webhook_url "$hook"
    ;;
esac
