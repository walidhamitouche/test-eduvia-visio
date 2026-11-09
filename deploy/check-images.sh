#!/bin/sh
# Contrôle de sécurité : aucun secret en clair dans les images (historique, variables, fichiers).
# Usage : ./check-images.sh   (images déjà construites)
set -eu
fail=0
for img in eduvia-visio-api eduvia-visio-transcriber; do
  echo "== $img"
  docker history --no-trunc "$img" | grep -i -E 'DOTENV_PRIVATE_KEY|API_KEY=|SECRET=|PASSWORD' && { echo "SECRET dans l'historique"; fail=1; } || echo "historique : rien"
  docker inspect "$img" --format '{{json .Config.Env}}' | grep -i -E 'DOTENV_PRIVATE_KEY|SECRET|PASSWORD' && { echo "SECRET dans l'environnement"; fail=1; } || echo "environnement : rien"
  docker run --rm --entrypoint sh "$img" -c '! ls -a /app | grep -E "^\.env\.keys" && grep -c "encrypted:" /app/.env.production; ! grep -E "^[A-Z_]+=[^\"e]" /app/.env.production | grep -v -E "^DOTENV_PUBLIC_KEY"' && echo "fichiers : .env.production chiffré, pas de .env.keys" || { echo "valeur en clair ou clé privée dans l'image"; fail=1; }
done
exit $fail
