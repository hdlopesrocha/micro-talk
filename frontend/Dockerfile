# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Build the Vue 3 single-page application.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app
ENV CI=true

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/index.html ./index.html
COPY frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts ./
COPY frontend/src ./src

# Install dev dependencies above, but compile with the production Vue runtime.
RUN NODE_ENV=production npm run build

# ---------------------------------------------------------------------------
# Serve the static files and reverse proxy the API to the gateway.
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
