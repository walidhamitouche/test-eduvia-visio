# Lance LiveKit Server en local pour le developpement (etape 6).
# Usage, depuis la racine du depot :   .\deploy\dev\livekit-dev.ps1
#
# Prerequis : le binaire livekit-server.exe dans le PATH (ou dans deploy\dev\), telecharge depuis
# https://github.com/livekit/livekit/releases (archive "windows_amd64").
#
# La cle et le secret sont lus dans .env.development (chiffre) : le backend et LiveKit utilisent
# ainsi exactement les memes valeurs, et rien n'est ecrit en clair dans le depot.

$ErrorActionPreference = "Stop"

$key = dotenvx get LIVEKIT_API_KEY -f .env.development
$secret = dotenvx get LIVEKIT_API_SECRET -f .env.development
if (-not $key -or -not $secret) {
    Write-Error "LIVEKIT_API_KEY / LIVEKIT_API_SECRET introuvables dans .env.development"
}

$server = Get-Command livekit-server -ErrorAction SilentlyContinue
if (-not $server -and (Test-Path "$PSScriptRoot\livekit-server.exe")) {
    $server = "$PSScriptRoot\livekit-server.exe"
}
if (-not $server) {
    Write-Error "livekit-server.exe introuvable : telecharge-le, mets-le dans le PATH ou dans deploy\dev\"
}

# Variable d'environnement reconnue par LiveKit : "cle: secret". Visible seulement dans ce processus.
$env:LIVEKIT_KEYS = "${key}: ${secret}"

# Etape 8 a 12 : LiveKit previent le backend (presence, sorties) sur /v1/livekit/webhook, signe avec
# la meme cle. Le backend doit tourner sur le port 8000 (sinon change l'URL ci-dessous).
$config = Join-Path $env:TEMP "livekit-dev.yaml"
@"
webhook:
  api_key: $key
  urls:
    - http://localhost:8000/v1/livekit/webhook
"@ | Set-Content -Encoding ascii $config
& $server --config $config
