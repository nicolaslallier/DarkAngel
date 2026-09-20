#!/bin/sh
# Publish the built SPA into the volume the Infra NGINX serves.
#
# This container is the whole "frontend service" at runtime: it copies the
# build into the shared `darkangel-web` volume and exits. It serves no HTTP --
# the Infra NGINX is the only web server in front of DarkAngel, and it reads
# these files straight off that volume (deploy/nginx/darkangel.conf).
set -eu

root=${PUBLISH_ROOT:-/srv/darkangel}

# Stage into next/ and swap, so NGINX keeps serving the previous current/
# through the copy instead of a half-written directory. The swap is two
# renames inside one volume: as close to atomic as a directory swap gets.
rm -rf "$root/next" "$root/previous"
mkdir -p "$root/next"
cp -a /opt/darkangel/dist/. "$root/next/"

if [ -d "$root/current" ]; then
	mv "$root/current" "$root/previous"
fi
mv "$root/next" "$root/current"
rm -rf "$root/previous"

echo "published $(find "$root/current" -type f | wc -l) files to $root/current"
