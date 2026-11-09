#!/bin/sh
# Renders nginx.conf from its template, then runs nginx in the foreground.
#
# Only the variables listed below are substituted, so nginx's own $variables are left alone.
# Defaults match docker-compose (Docker DNS, service names). On ECS, where every container
# shares one task network namespace, set the upstreams to 127.0.0.1:<port> and the resolver
# to the Amazon DNS address.
set -eu

export NGINX_RESOLVER="${NGINX_RESOLVER:-127.0.0.11}"
export BACKEND_UPSTREAM="${BACKEND_UPSTREAM:-backend:8000}"
export ATX_ANALYSIS_UPSTREAM="${ATX_ANALYSIS_UPSTREAM:-atx-analysis-agent:8004}"
export ATX_TRANSFORM_UPSTREAM="${ATX_TRANSFORM_UPSTREAM:-atx-transform-agent:8005}"

envsubst '${NGINX_RESOLVER} ${BACKEND_UPSTREAM} ${ATX_ANALYSIS_UPSTREAM} ${ATX_TRANSFORM_UPSTREAM}' \
  < /etc/nginx/nginx.conf.template > /tmp/nginx.conf

exec nginx -c /tmp/nginx.conf -g 'daemon off;'
