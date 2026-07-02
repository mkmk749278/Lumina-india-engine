#!/usr/bin/env bash
# Nginx reverse proxy setup for the India engine API.
# Run once on the VPS — creates the site config and reloads nginx.
# SSL is handled by Cloudflare (Full mode) — nginx listens on 80.
set -euo pipefail

API_PORT="${API_PORT:-8000}"

cat > /etc/nginx/sites-available/india-engine <<NGINX
upstream india_api {
    server 127.0.0.1:${API_PORT};
    keepalive 16;
}

limit_req_zone \$binary_remote_addr zone=india_api_limit:10m rate=60r/m;

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # Rate limit
    limit_req zone=india_api_limit burst=30 nodelay;

    # Security: block probes
    location ~* \.(php|asp|aspx|jsp|cgi)$ { return 444; }
    location ~ /\.(git|env|htaccess) { return 444; }

    # API proxy
    location /api/ {
        proxy_pass http://india_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
        proxy_connect_timeout 5s;
    }

    # Root — health redirect
    location = / {
        return 302 /api/health;
    }

    location / {
        return 404;
    }
}
NGINX

# Enable the site (remove default if it exists)
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/india-engine /etc/nginx/sites-enabled/india-engine

# Test and reload
nginx -t
systemctl reload nginx

echo "Nginx configured — proxying port 80 → 127.0.0.1:${API_PORT}"
