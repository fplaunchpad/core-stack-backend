# check=skip=SecretsUsedInArgOrEnv
# Builds one CoRE Stack React website (landscape-explorer or admin-dashboard)
# and serves the static files with nginx. Used by frontend.compose.yml; the
# build context is the website's own git checkout, unchanged.
#
# Create React App bakes REACT_APP_* values into the JavaScript at build time,
# so they are build arguments here. Change them in nrm_app/.env and rebuild.
#
# The check= line above silences Docker's warning about REACT_APP_API_KEY as a
# build argument: Create React App puts it in the public JavaScript anyway.
#
# frontend.Dockerfile.dockerignore (next to this file) keeps node_modules,
# build output, .env files and .git out of the image.

FROM node:22-bookworm-slim AS build
WORKDIR /src
# Packages first, so a rebuild after a code change reuses the installed packages.
COPY package.json package-lock.jso[n] ./
RUN npm install --no-audit --no-fund --loglevel=error
COPY . .
RUN rm -f .env .env.local .env.production .env.production.local

ARG REACT_APP_API_URL=
ARG REACT_APP_BASEURL=
ARG REACT_APP_GEOSERVER_URL=
ARG REACT_APP_API_KEY=
ARG REACT_APP_LOCAL_API_BASE_URL=
ARG REACT_APP_IMAGE_LAYER_URL=
# Source maps help debug crashes in the browser; off by default to keep the image small.
ARG GENERATE_SOURCEMAP=false
ENV CI=false \
    DISABLE_ESLINT_PLUGIN=true \
    GENERATE_SOURCEMAP=${GENERATE_SOURCEMAP} \
    NODE_OPTIONS=--max-old-space-size=4096
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=setup nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/build /usr/share/nginx/html
