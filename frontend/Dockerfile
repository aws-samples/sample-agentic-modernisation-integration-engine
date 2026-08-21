# syntax=docker/dockerfile:1

# =============================================================================
# Code Transformation Engine - Frontend image (multi-stage)
#
#   Stage 1 (build): Node 20 compiles the Vite + React 18 + TypeScript SPA
#                    (npm ci -> npm run build) into /app/dist.
#   Stage 2 (serve): Nginx serves the static bundle and reverse-proxies to
#                    every backend agent (see nginx.conf).
#
# Built for linux/arm64 (docker-compose sets platform: linux/arm64, matching
# ECS Fargate + AgentCore Runtime). Runs as a non-root user and is compatible
# with docker-compose `security_opt: no-new-privileges:true`.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 - Build the React SPA
# -----------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Install dependencies from the lockfile first for reproducible, cache-friendly
# builds. `npm ci` requires package-lock.json (generated in task 41).
COPY package.json package-lock.json ./
RUN npm ci

# Copy the rest of the source and produce the production bundle in /app/dist
# (Vite build.outDir = "dist"; the "build" script runs `tsc && vite build`).
COPY . .
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2 - Serve with Nginx
# -----------------------------------------------------------------------------
FROM nginx:1.27-alpine AS serve

# wget (busybox) is used by the health check below and by docker-compose.
# Replace the default site config with our reverse-proxy configuration.
COPY nginx.conf /etc/nginx/nginx.conf

# Ship the compiled SPA. The build stage emits to /app/dist (Vite outDir).
COPY --from=build /app/dist /usr/share/nginx/html

# Prepare writable temp/cache + pid locations and hand ownership to the
# unprivileged `nginx` user (uid/gid 101) so the server can run non-root.
RUN mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi \
             /tmp/nginx/uwsgi /tmp/nginx/scgi \
    && touch /tmp/nginx.pid \
    && chown -R nginx:nginx /tmp/nginx /tmp/nginx.pid /usr/share/nginx/html \
    && chown nginx:nginx /etc/nginx/nginx.conf

USER nginx

EXPOSE 3000

# Verify the SPA is being served (matches docker-compose's wget check).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:3000/ || exit 1

# Run nginx in the foreground (daemon off) so it is PID 1 in the container.
CMD ["nginx", "-g", "daemon off;"]
