#!/bin/sh
# Point d'entrée unique. Seule variable de secret attendue : DOTENV_PRIVATE_KEY_PRODUCTION.
set -eu

if [ -z "${DOTENV_PRIVATE_KEY_PRODUCTION:-}" ]; then
  echo "DOTENV_PRIVATE_KEY_PRODUCTION manquante : voir docs/INSTALL.md" >&2
  exit 1
fi

run() { exec dotenvx run -f .env.production -q -- "$@"; }

case "${1:-api}" in
  api)
    # Migrations d'abord (idempotentes), puis le serveur. Un seul processus : le planificateur
    # tourne dans l'API (SQLite = une seule instance).
    dotenvx run -f .env.production -q -- python -m visio.db.migrate
    run uvicorn visio.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
    ;;
  transcriber) run python -m visio.workers.transcriber ;;
  model-init)  run python -m visio.tools.modelinit ;;
  config-init) run python -m visio.tools.renderconfig "${2:-/generated}" ;;
  backup)      run python -m visio.tools.dbbackup backup "$2" ;;
  restore)     run python -m visio.tools.dbbackup restore "$2" ;;
  *)           exec "$@" ;;
esac
