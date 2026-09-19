# syntax=docker/dockerfile:1

FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `npm run build` is vue-tsc type-check + vite build, so a type error fails here.
RUN npm run build

# No web server in this image on purpose. The Infra stack's NGINX is the only
# one that fronts DarkAngel (deploy/nginx/darkangel.conf); this stage is a
# one-shot publisher that hands it the files through a shared volume and exits.
FROM busybox:1.37-musl

COPY --from=build /app/dist /opt/darkangel/dist
COPY --chmod=0755 publish-assets.sh /usr/local/bin/publish-assets

CMD ["/usr/local/bin/publish-assets"]
