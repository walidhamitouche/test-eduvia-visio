#!/bin/sh
# Sauvegarde cohérente de la base, à lancer sur le serveur (cron). Usage :
#   DOTENV_PRIVATE_KEY_PRODUCTION=... ./backup.sh [dossier]   (défaut /data/eduvia/backups)
# Conserve les 14 dernières. Restauration : docs/INSTALL.md §8.
set -eu
DIR="${1:-/data/eduvia/backups}"; mkdir -p "$DIR"
NAME="visio-$(date +%Y%m%d-%H%M%S).db"
cd "$(dirname "$0")"
docker compose run --rm --no-deps -v "$DIR:/backups" api backup "/backups/$NAME"
ls -1t "$DIR"/visio-*.db | tail -n +15 | xargs -r rm -f
echo "OK : $DIR/$NAME"
