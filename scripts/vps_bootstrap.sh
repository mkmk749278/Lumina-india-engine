#!/usr/bin/env bash
# vps_bootstrap.sh — Bootstrap a fresh India VPS for the Lumin India engine.
#
# Installs Docker, nginx, UFW firewall, and configures Cloudflare Origin CA
# SSL so lumintrade.app can proxy to this server.
#
# Run on the VPS as root:
#   sudo bash scripts/vps_bootstrap.sh --domain lumintrade.app
#
# Prerequisites:
#   - Fresh Ubuntu 22.04+ VPS with a static IP
#   - DNS A record pointing lumintrade.app → this VPS IP (via Cloudflare)
#   - Cloudflare SSL/TLS mode set to "Full (Strict)"
#
# After running, you still need to:
#   1. Paste Cloudflare Origin CA cert + key (script will prompt)
#   2. Deploy the engine via GitHub Actions (push to main)
set -euo pipefail

# ── Output helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  $*${NC}"; }
warn() { echo -e "${YELLOW}  $*${NC}"; }
err()  { echo -e "${RED}  $*${NC}" >&2; }
info() { echo -e "${CYAN}  $*${NC}"; }
hdr()  { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}"; }

# ── Defaults ────────────────────────────────────────────────────────────────
DOMAIN="${INDIA_DOMAIN:-lumintrade.app}"
ENGINE_DIR="${ENGINE_DIR:-/opt/lumin-india}"
CERT_DIR="/etc/ssl/cloudflare"
API_PORT=8000
SSH_PORT="${SSH_PORT:-22}"

# ── Argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)      DOMAIN="$2"; shift 2 ;;
        --engine-dir)  ENGINE_DIR="$2"; shift 2 ;;
        --ssh-port)    SSH_PORT="$2"; shift 2 ;;
        -h|--help)     sed -n '2,14p' "$0"; exit 0 ;;
        *)             err "Unknown argument: $1"; exit 1 ;;
    esac
done

# ── Pre-flight ──────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "Run as root: sudo bash scripts/vps_bootstrap.sh"
    exit 1
fi

hdr "Lumin India VPS Bootstrap"
info "Domain:     $DOMAIN"
info "Engine dir: $ENGINE_DIR"
info "SSH port:   $SSH_PORT"
echo

# ── PHASE 1 — System packages ──────────────────────────────────────────────
hdr "PHASE 1 — System update + base packages"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq >/dev/null
apt-get install -y -qq curl git ufw nginx >/dev/null
ok "Base packages installed"

# ── PHASE 2 — Docker ───────────────────────────────────────────────────────
hdr "PHASE 2 — Docker + Docker Compose"

if command -v docker &>/dev/null; then
    ok "Docker already installed: $(docker --version)"
else
    info "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker --quiet
    systemctl start docker
    ok "Docker installed: $(docker --version)"
fi

if docker compose version &>/dev/null; then
    ok "Docker Compose available: $(docker compose version --short)"
else
    err "Docker Compose plugin not found. Install it:"
    err "  apt-get install docker-compose-plugin"
    exit 1
fi

# ── PHASE 3 — UFW Firewall ─────────────────────────────────────────────────
hdr "PHASE 3 — UFW firewall"

ufw --force reset >/dev/null 2>&1
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "$SSH_PORT/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ok "UFW enabled: SSH($SSH_PORT), HTTP(80), HTTPS(443)"

# ── PHASE 4 — Cloudflare Origin CA cert ────────────────────────────────────
hdr "PHASE 4 — Cloudflare Origin CA certificate"

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"

CERT_FILE="$CERT_DIR/$DOMAIN.pem"
KEY_FILE="$CERT_DIR/$DOMAIN.key"

if [[ -f "$CERT_FILE" ]] && [[ -f "$KEY_FILE" ]]; then
    ok "Cloudflare Origin CA cert already exists at $CERT_FILE"
    info "To replace, delete the files and re-run this script."
else
    echo
    echo -e "${BOLD}You need a Cloudflare Origin CA certificate.${NC}"
    echo
    echo "  1. Go to Cloudflare Dashboard > SSL/TLS > Origin Server"
    echo "  2. Click 'Create Certificate'"
    echo "  3. Keep defaults (RSA 2048, 15 years, *.lumintrade.app + lumintrade.app)"
    echo "  4. Click 'Create'"
    echo "  5. Copy the CERTIFICATE (PEM) and PRIVATE KEY"
    echo

    # Certificate
    echo -e "${YELLOW}Paste the CERTIFICATE (PEM) below, then press Ctrl+D:${NC}"
    cat > "$CERT_FILE"
    chmod 600 "$CERT_FILE"

    # Private key
    echo -e "${YELLOW}Paste the PRIVATE KEY below, then press Ctrl+D:${NC}"
    cat > "$KEY_FILE"
    chmod 600 "$KEY_FILE"

    # Validate the cert was pasted correctly
    if ! openssl x509 -in "$CERT_FILE" -noout 2>/dev/null; then
        err "Certificate file does not look like valid PEM. Check and re-run."
        rm -f "$CERT_FILE" "$KEY_FILE"
        exit 1
    fi
    ok "Cloudflare Origin CA cert saved to $CERT_DIR/"
fi

# ── PHASE 5 — nginx config ─────────────────────────────────────────────────
hdr "PHASE 5 — nginx reverse proxy"

NGINX_SITE="lumin-india"

# Rate limit zone
cat > /etc/nginx/conf.d/lumin-india-ratelimit.conf <<'EOF_RL'
limit_req_zone $binary_remote_addr zone=lumin_india:10m rate=60r/m;
EOF_RL

cat > "/etc/nginx/sites-available/$NGINX_SITE" <<EOF_SITE
# Lumin India API — generated by vps_bootstrap.sh
# Re-run the script to regenerate; don't hand-edit.

upstream india_api {
    server 127.0.0.1:$API_PORT;
    keepalive 16;
}

# HTTPS (Cloudflare Origin CA)
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate     $CERT_FILE;
    ssl_certificate_key $KEY_FILE;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Cloudflare authenticates origin — restrict to CF IPs for extra safety.
    # Updated list: https://www.cloudflare.com/ips/
    # Uncomment and populate if you want IP-level lockdown:
    # allow 173.245.48.0/20;
    # allow 103.21.244.0/22;
    # ... (full list from CF)
    # deny all;

    location / {
        limit_req zone=lumin_india burst=30 nodelay;
        proxy_pass http://india_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
        proxy_connect_timeout 5s;

        # CORS preflight for the Lumin India app
        if (\$request_method = 'OPTIONS') {
            add_header 'Access-Control-Allow-Origin' '*' always;
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS' always;
            add_header 'Access-Control-Allow-Headers' 'Authorization, Content-Type' always;
            add_header 'Access-Control-Max-Age' 86400;
            add_header 'Content-Length' 0;
            return 204;
        }
    }

    # Block probes
    location ~* \.(php|asp|aspx|jsp)$ { return 444; }
    location ~ /\.(git|env|svn|htaccess) { return 444; }

    access_log /var/log/nginx/lumin-india.access.log;
    error_log  /var/log/nginx/lumin-india.error.log warn;
}

# HTTP → HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}
EOF_SITE

# Activate site, remove default
ln -sf "/etc/nginx/sites-available/$NGINX_SITE" "/etc/nginx/sites-enabled/$NGINX_SITE"
rm -f /etc/nginx/sites-enabled/default

if nginx -t 2>&1 | grep -q "successful"; then
    systemctl enable nginx --quiet
    systemctl reload nginx
    ok "nginx configured and reloaded"
else
    err "nginx config test failed:"
    nginx -t
    exit 1
fi

# ── PHASE 6 — Engine directory ──────────────────────────────────────────────
hdr "PHASE 6 — Engine directory"

mkdir -p "$ENGINE_DIR"
mkdir -p "$ENGINE_DIR/data"
ok "Engine directory created at $ENGINE_DIR"
info "GitHub Actions will clone the repo here on deploy."

# ── Done ────────────────────────────────────────────────────────────────────
hdr "VPS bootstrap complete"

PUBLIC_IP=$(curl -4 -s --max-time 5 ifconfig.me 2>/dev/null || echo "<unknown>")

cat <<EOF_DONE

  Domain:    $DOMAIN
  VPS IP:    $PUBLIC_IP
  nginx:     HTTPS on 443 (Cloudflare Origin CA) + HTTP→HTTPS redirect
  Firewall:  SSH($SSH_PORT) + HTTP(80) + HTTPS(443)
  Docker:    $(docker --version)
  Engine:    $ENGINE_DIR (empty — deploy via GitHub Actions)

  Cloudflare settings needed:
    SSL/TLS mode:  Full (Strict)
    DNS A record:  $DOMAIN -> $PUBLIC_IP  (proxied, orange cloud)

  Next steps:
    1. Verify Cloudflare SSL mode is "Full (Strict)"
    2. Test: curl -I https://$DOMAIN  (should get 502 until engine is deployed)
    3. Set up GitHub Actions deploy workflow to push to $ENGINE_DIR
    4. Deploy the engine: push to main

EOF_DONE
