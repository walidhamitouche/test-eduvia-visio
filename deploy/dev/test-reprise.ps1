# Demo AUTOMATIQUE de l'etape 17d (reprise apres panne d'un conteneur) : rien a taper.
# A lancer depuis la racine du depot, avec Docker de dev (docker-dev.ps1 up) et l'API
# (uv run task dev-lan) deja lances.
#
#   .\deploy\dev\test-reprise.ps1                 # pytest + coupure de LiveKit puis du S3
#   .\deploy\dev\test-reprise.ps1 -SansPytest     # saute les tests automatiques
#   .\deploy\dev\test-reprise.ps1 -Services livekit,s3,redis   # choisir les conteneurs a couper
#
# Pour chaque conteneur : 1. il est ARRETE pour de vrai ; 2. /v1/health passe a 503 (l'API reste
# vivante : GET /health repond toujours 200) ; 3. il est REDEMARRE ; 4. /v1/health revient a 200 ;
# 5. jobs-list ne montre aucun echec cree par la coupure. Le conteneur est TOUJOURS rallume a la fin.
# Le redemarrage de l'API elle-meme (Ctrl+C puis relance) est couvert par pytest
# (tests/integration/test_restart.py) et par la verification manuelle de DECISIONS.md (17d).
# Code de sortie 0 = tout vert.

param(
    [switch]$SansPytest,
    [string[]]$Services = @("livekit", "s3")
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$base = "http://localhost:8000"
$compose = Join-Path $PSScriptRoot "..\docker-compose.dev.yml"
$key = (dotenvx get API_KEY -f .env.development)
$script:ok = 0
$script:ko = 0

function Check([string]$nom, [bool]$condition, [string]$detail = "") {
    if ($condition) { $script:ok++; Write-Host "  [OK]    $nom" -ForegroundColor Green }
    else { $script:ko++; Write-Host "  [ECHEC] $nom   $detail" -ForegroundColor Red }
}

function Titre([string]$t) { Write-Host "`n=== $t" -ForegroundColor Cyan }

function Code-Http([string]$chemin, [bool]$avecCle) {
    $a = @("-s", "-o", "NUL", "-m", "15", "-w", "%{http_code}")
    if ($avecCle) { $a += @("-H", "Authorization: Bearer $key") }
    return (curl.exe @a "$base$chemin")
}

function Attendre-Code([string]$chemin, [string]$attendu, [int]$max = 90) {
    $debut = Get-Date
    do {
        if ((Code-Http $chemin $true) -eq $attendu) { return $true }
        Start-Sleep -Seconds 2
    } while (((Get-Date) - $debut).TotalSeconds -lt $max)
    return $false
}

if (-not $SansPytest) {
    Titre "1. pytest : redemarrages (tests/integration/test_restart.py)"
    uv run pytest tests/integration/test_restart.py -q
    Check "pytest test_restart.py" ($LASTEXITCODE -eq 0)
}

Titre "2. L'API repond avant la demo"
Check "GET /health public = 200" ((Code-Http "/health" $false) -eq "200")
Check "GET /v1/health = 200 (tout est sain au depart)" ((Attendre-Code "/v1/health" "200" 30)) "lance docker-dev.ps1 up d'abord"


function Ids-Echecs { (uv run task jobs-list 2>&1 | Out-String) | Select-String -Pattern '\b[a-z]{3,4}_[A-Za-z0-9]{6,}\b' -AllMatches | ForEach-Object { $_.Matches.Value } | Sort-Object -Unique }
$avant = @(Ids-Echecs)


foreach ($service in $Services) {
    Titre "3. Coupure du conteneur '$service'"
    try {
        docker compose -f $compose stop $service 2>&1 | Out-Null
        Check "/v1/health passe a 503 sans '$service'" (Attendre-Code "/v1/health" "503" 60)
        Check "GET /health reste 200 (l'API n'est pas redemarree par la sonde)" ((Code-Http "/health" $false) -eq "200")
    } finally {
        docker compose -f $compose start $service 2>&1 | Out-Null
    }
    Check "/v1/health revient a 200 apres redemarrage de '$service'" (Attendre-Code "/v1/health" "200" 120)
    $nouveaux = @(Ids-Echecs | Where-Object { $avant -notcontains $_ })
    Check "jobs-list : aucun NOUVEL echec apres la coupure" ($nouveaux.Count -eq 0) ($nouveaux -join ", ")
}

Write-Host "`n$($script:ok) OK, $($script:ko) en echec" -ForegroundColor $(if ($script:ko -eq 0) { "Green" } else { "Red" })
exit $(if ($script:ko -eq 0) { 0 } else { 1 })
