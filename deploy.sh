#!/usr/bin/env bash
# Lumin India Engine — Docker deployment script.
# Called by the GitHub Actions deploy workflow on the VPS.
set -euo pipefail

echo "Lumin India Engine — Docker Deployment"
echo "======================================="

# ── Argument parsing ────────────────────────────────────────────────────
DO_CLEAN=false
for arg in "$@"; do
    case "$arg" in
        --clean) DO_CLEAN=true ;;
        *) echo "Unknown argument: $arg"; echo "Usage: $0 [--clean]"; exit 1 ;;
    esac
done

# ── Prerequisites ───────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || {
    echo "Docker not installed."; exit 1
}
docker compose version >/dev/null 2>&1 || {
    echo "Docker Compose not installed."; exit 1
}

COMPOSE_FILE="docker-compose.india.yml"

# ── Clean mode ──────────────────────────────────────────────────────────
if [ "$DO_CLEAN" = true ]; then
    echo ""
    echo "Cleaning Docker state..."
    docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
    docker system prune -af 2>/dev/null || true
    docker builder prune -af 2>/dev/null || true
    echo "Cleanup complete."
    echo ""
fi

# ── .env ────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
fi

# ── Build ───────────────────────────────────────────────────────────────
BUILD_ARGS=()
if [ "$DO_CLEAN" = true ]; then
    BUILD_ARGS=(--no-cache)
fi

echo "Building..."
docker compose -f "$COMPOSE_FILE" build "${BUILD_ARGS[@]}"

echo "Starting..."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo ""
echo "Deployment complete!"
echo ""
echo "Status:"
docker compose -f "$COMPOSE_FILE" ps

# ── Health check ────────────────────────────────────────────────────────
echo ""
echo "Health check (up to 120s)..."

API_PORT=$(grep -oP '^API_PORT=\K.*' .env 2>/dev/null || echo "8000")
API_PORT="${API_PORT:-8000}"

_ok=false
for _i in $(seq 1 24); do
    _http=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${API_PORT}/api/health" 2>/dev/null || echo "000")
    if [ "$_http" = "200" ]; then
        _ok=true
        break
    fi
    sleep 5
done
if [ "$_ok" = true ]; then
    echo "Engine healthy — API responding on port ${API_PORT}."
else
    echo "API not responding within 120s. Check:"
    echo "    docker logs india-engine --tail 50"
fi
