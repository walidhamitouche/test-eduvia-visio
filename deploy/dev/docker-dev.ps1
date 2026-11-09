# Pilote l'environnement Docker de dev (etape 13).
# Usage, depuis la racine du depot :
#   .\deploy\dev\docker-dev.ps1 up        # genere les configs, demarre tout, cree le bucket S3
#   .\deploy\dev\docker-dev.ps1 status    # etat des services
#   .\deploy\dev\docker-dev.ps1 logs      # journaux LiveKit (Ctrl+C pour quitter)
#   .\deploy\dev\docker-dev.ps1 logs egress   # journaux d'un autre service (redis, s3, egress)
#   .\deploy\dev\docker-dev.ps1 down      # arrete (les donnees restent)
#   .\deploy\dev\docker-dev.ps1 reset     # arrete ET efface les volumes (repart de zero)
#
# Prerequis : Docker Desktop lance. Arreter avant tout le LiveKit "natif" (livekit-dev.ps1) :
# les deux utilisent le port 7880.
# Le backend doit ecouter sur toutes les interfaces pour recevoir le webhook de LiveKit depuis
# Docker :  uv run task dev-lan   (et non  uv run task dev).
param(
    [Parameter(Position = 0)][ValidateSet("up", "status", "logs", "down", "reset")][string]$Action = "up",
    [Parameter(Position = 1)][ValidateSet("livekit", "redis", "s3", "egress")][string]$Service = "livekit"
)

$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "..\docker-compose.dev.yml"
$gen = Join-Path $PSScriptRoot ".generated"

function Lire-Dotenvx([string]$nom) {
    $valeur = dotenvx get $nom -f .env.development
    if (-not $valeur) { Write-Error "$nom introuvable dans .env.development" }
    return $valeur
}

function Obtenir-IpLan {
    # Adresse que LiveKit annonce aux clients WebRTC (node_ip). Chrome n'etablit pas de connexion
    # WebRTC vers 127.0.0.1 : il faut une vraie adresse du poste (celle de la carte reseau qui a
    # la passerelle par defaut). Forcer une autre adresse :  $env:VISIO_DEV_IP = "192.168.1.17"
    if ($env:VISIO_DEV_IP) { return $env:VISIO_DEV_IP }
    $cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } | Select-Object -First 1
    if (-not $cfg) { Write-Error "Adresse IP locale introuvable : definis `$env:VISIO_DEV_IP (ex. 192.168.1.17)" }
    return $cfg.IPv4Address.IPAddress
}

function Ecrire-ConfigLiveKit {
    $key = Lire-Dotenvx "LIVEKIT_API_KEY"
    $secret = Lire-Dotenvx "LIVEKIT_API_SECRET"
    $ip = Obtenir-IpLan
    Write-Host "node_ip LiveKit (adresse LAN du poste) : $ip" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force $gen | Out-Null
    New-Item -ItemType Directory -Force (Join-Path $gen "certs") | Out-Null   # certificat TURN (mkcert), etape 18
    # Valeurs entre guillemets : un secret contenant ':' ou '#' ne casse pas le YAML.
    $yaml = @"
port: 7880
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false
  node_ip: $ip
redis:
  address: redis:6379
keys:
  "${key}": "${secret}"
webhook:
  api_key: "$key"
  urls:
    - http://host.docker.internal:8000/v1/livekit/webhook
"@
    # TURN de dev (etape 18), facultatif :  $env:VISIO_DEV_TURN = "udp"  ou  "tls"  avant  docker-dev.ps1 up
    #   udp : TURN/UDP sur 3478.  tls : TURN/TLS seul sur 5349 (certificat mkcert dans .generated\certs).
    if ($env:VISIO_DEV_TURN -eq "udp") {
        $yaml += "`nturn:`n  enabled: true`n  udp_port: 3478`n  relay_range_start: 30000`n  relay_range_end: 30010`n"
    } elseif ($env:VISIO_DEV_TURN -eq "tls") {
        $yaml += "`nturn:`n  enabled: true`n  domain: turn.localtest.me`n  tls_port: 5349`n  cert_file: /certs/turn.crt`n  key_file: /certs/turn.key`n  relay_range_start: 30000`n  relay_range_end: 30010`n"
    }
    $yaml | Set-Content -Encoding ascii (Join-Path $gen "livekit.yaml")
}

function Ecrire-ConfigS3 {
    # Meme cle et meme secret que ceux que l'API lit (S3_ACCESS_KEY / S3_SECRET_KEY) : le S3 de
    # dev et le backend concordent, et rien n'est ecrit en clair dans le depot (.generated est ignore).
    $key = Lire-Dotenvx "S3_ACCESS_KEY"
    $secret = Lire-Dotenvx "S3_SECRET_KEY"
    New-Item -ItemType Directory -Force $gen | Out-Null
    @{
        identities = @(@{
            name        = "visio"
            credentials = @(@{ accessKey = $key; secretKey = $secret })
            actions     = @("Admin", "Read", "Write", "List", "Tagging")
        })
    } | ConvertTo-Json -Depth 6 | Set-Content -Encoding ascii (Join-Path $gen "s3.json")
}

function Ecrire-ConfigEgress {
    # Egress est dans le reseau Docker normal : LiveKit, Redis et le S3 sont joints par leur nom
    # de service (livekit:7880, redis:6379, s3:9000). Les identifiants sont ceux que l'API lit.
    $key = Lire-Dotenvx "LIVEKIT_API_KEY"
    $secret = Lire-Dotenvx "LIVEKIT_API_SECRET"
    $s3key = Lire-Dotenvx "S3_ACCESS_KEY"
    $s3secret = Lire-Dotenvx "S3_SECRET_KEY"
    $bucket = Lire-Dotenvx "S3_BUCKET"
    $region = Lire-Dotenvx "S3_REGION"
    New-Item -ItemType Directory -Force $gen | Out-Null
    @"
logging:
  level: info
api_key: "$key"
api_secret: "$secret"
ws_url: ws://livekit:7880
insecure: true
redis:
  address: redis:6379
health_port: 8080
s3:
  access_key: "$s3key"
  secret: "$s3secret"
  region: "$region"
  endpoint: http://s3:9000
  bucket: "$bucket"
  force_path_style: true
storage:
  s3:
    access_key: "$s3key"
    secret: "$s3secret"
    region: "$region"
    endpoint: http://s3:9000
    bucket: "$bucket"
    force_path_style: true
"@ | Set-Content -Encoding ascii (Join-Path $gen "egress.yaml")
}

function Attendre-LiveKit {
    # LiveKit n'a pas de healthcheck Docker (image sans shell) : on interroge son port HTTP.
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:7880" -TimeoutSec 2
            if ($r.StatusCode -eq 200) { Write-Host "LiveKit repond sur http://127.0.0.1:7880" -ForegroundColor Green; return }
        } catch { Start-Sleep -Seconds 1 }
    }
    Write-Error "LiveKit ne repond pas apres 30 s : .\deploy\dev\docker-dev.ps1 logs"
}

# Docker absent ou arrete : message clair (sans que la sortie d'erreur de docker ne leve une exception).
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker info *> $null
$dockerOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $previous
if (-not $dockerOk) { Write-Error "Docker Desktop ne repond pas : lance-le et attends qu'il soit pret." }

switch ($Action) {
    "up" {
        if (Get-Process livekit-server -ErrorAction SilentlyContinue) {
            Write-Error "livekit-server (natif) tourne encore : arrete le terminal 1 avant (port 7880)."
        }
        Ecrire-ConfigLiveKit
        Ecrire-ConfigS3
        Ecrire-ConfigEgress
        # Le S3 (SeaweedFS) n'est pas une dependance de livekit : sans cette ligne il reste eteint
        # apres un redemarrage de Docker (les ecritures de transcription et d'enregistrement
        # echouent alors avec "S3 injoignable : EndpointConnectionError").
        docker compose -f $compose up -d --wait s3
        # --force-recreate sur egress : il relit ainsi egress.yaml (regenere a chaque "up").
        docker compose -f $compose up -d --wait --force-recreate livekit
        docker compose -f $compose up -d --force-recreate --no-deps egress
        docker compose -f $compose ps
        Attendre-LiveKit
        # Cree le bucket de dev (S3_BUCKET) s'il manque ; attend que le S3 reponde.
        uv run task s3-init
        if ($LASTEXITCODE -ne 0) { Write-Error "Creation du bucket impossible : .\deploy\dev\docker-dev.ps1 logs s3" }
        # Enregistre 5 s d'une page web et verifie que le MP4 arrive sur le S3 (jusqu'a 90 s d'attente).
        uv run task egress-check
        if ($LASTEXITCODE -ne 0) { Write-Error "Egress ne fonctionne pas : .\deploy\dev\docker-dev.ps1 logs egress" }
    }
    "status" { docker compose -f $compose ps }
    "logs"   { docker compose -f $compose logs -f $Service }
    "down"   { docker compose -f $compose down }
    "reset"  {
        docker compose -f $compose down -v --remove-orphans
        if (Test-Path $gen) { Remove-Item -Recurse -Force $gen }
        Write-Host "Environnement Docker remis a zero." -ForegroundColor Yellow
    }
}
