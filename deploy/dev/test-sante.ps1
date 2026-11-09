# Test AUTOMATIQUE de l'etape 17b (/health enrichi) : tout est enchaine, rien a taper.
# A lancer depuis la racine du depot, avec Docker de dev (docker-dev.ps1 up) et l'API
# (uv run task dev-lan) deja lances.
#
#   .\deploy\dev\test-sante.ps1                 # tout : tests pytest + coupures reelles S3 / LiveKit + file de taches
#   .\deploy\dev\test-sante.ps1 -Nettoyer       # + supprime d'abord les anciens echecs de la base de dev
#   .\deploy\dev\test-sante.ps1 -SansPytest     # saute les 50 tests automatiques (plus rapide)
#   .\deploy\dev\test-sante.ps1 -SansDocker     # saute les coupures S3 / LiveKit
#
# Ce que fait le script :
#   1. pytest : tests/integration/test_health_detail.py + tests/unit/test_health.py
#   2. route publique /health et refus sans cle / avec fausse cle
#   3. etat de depart "ok" (sinon : -Nettoyer), champs de la reponse, rien de sensible
#   4. coupe le S3 -> 503 degraded -> le rallume -> retour a 200 ok
#   5. coupe LiveKit -> idem
#   6. coupe les deux -> la route repond en moins de 5 s, 503
#   7. injecte une tache abandonnee -> 503 degraded -> la retire -> 200 ok
# Les conteneurs sont TOUJOURS rallumes a la fin, meme si un test echoue. Code de sortie 0 = tout vert.

param(
    [switch]$Nettoyer,
    [switch]$SansPytest,
    [switch]$SansDocker
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$base = "http://localhost:8000"
$compose = Join-Path $PSScriptRoot "..\docker-compose.dev.yml"
$key = (dotenvx get API_KEY -f .env.development)
$bucket = (dotenvx get S3_BUCKET -f .env.development)
$dbUrl = (dotenvx get DATABASE_URL -f .env.development)
$dbPath = $dbUrl -replace '^sqlite:///', ''
$idTest = "job_test17b"
$script:ok = 0
$script:ko = 0
$script:corps = @()   # toutes les reponses vues : on y cherche des fuites a la fin

function Check([string]$nom, [bool]$condition, [string]$detail = "") {
    if ($condition) {
        $script:ok++
        Write-Host "  [OK]    $nom" -ForegroundColor Green
    } else {
        $script:ko++
        Write-Host "  [ECHEC] $nom   $detail" -ForegroundColor Red
    }
}

function Titre([string]$t) { Write-Host "`n=== $t" -ForegroundColor Cyan }

function Appel([string]$chemin, [string]$cle = $key) {
    $a = @("-s", "-m", "15", "-w", "`n%{http_code}")
    if ($cle) { $a += @("-H", "Authorization: Bearer $cle") }
    $a += "$base$chemin"
    $sortie = @(curl.exe @a)
    $code = 0
    if ($sortie.Count -gt 0) { [void][int]::TryParse($sortie[-1], [ref]$code) }
    $texte = ""
    if ($sortie.Count -gt 1) { $texte = ($sortie[0..($sortie.Count - 2)] -join "`n") }
    $json = $null
    try { $json = $texte | ConvertFrom-Json } catch { }
    $script:corps += $texte
    return [pscustomobject]@{ Code = $code; Texte = $texte; Json = $json }
}

# Attend (jusqu'a $max secondes) que /v1/health rende le statut voulu. Rend le dernier appel.
function Attendre-Statut([string]$statut, [int]$max = 120) {
    $debut = Get-Date
    do {
        $r = Appel "/v1/health"
        if ($r.Json -and $r.Json.status -eq $statut) { return $r }
        Start-Sleep -Seconds 2
    } while (((Get-Date) - $debut).TotalSeconds -lt $max)
    return $r
}

function Docker-Compose { docker compose -f $compose @args 2>&1 | Out-Null }

function Py([string]$code, [string[]]$arguments) {
    $code | uv run python - $dbPath @arguments
}

$pyNettoyer = @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("delete from jobs where status in ('abandoned','failed')")
c.execute("delete from webhook_attempts where delivery_id in (select id from webhook_deliveries where status in ('pending','abandoned'))")
c.execute("delete from webhook_deliveries where status in ('pending','abandoned')")
c.commit()
'@

$pyInserer = @'
import sqlite3, sys, datetime as d
c = sqlite3.connect(sys.argv[1])
n = d.datetime.now(d.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
c.execute("delete from jobs where id = ?", (sys.argv[2],))
c.execute("insert into jobs(id,kind,payload,status,run_at,attempts,last_error,created_at,updated_at) values(?, 'transcribe', '{}', 'abandoned', ?, 5, 'test 17b', ?, ?)", (sys.argv[2], n, n, n))
c.commit()
'@

$pyRetirer = @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("delete from jobs where id = ?", (sys.argv[2],))
c.commit()
'@

try {
    # ---------------------------------------------------------------- 1. pytest
    if (-not $SansPytest) {
        Titre "1. Tests automatiques (pytest)"
        uv run pytest tests/integration/test_health_detail.py tests/unit/test_health.py -q
        Check "pytest : tous les tests de sante passent" ($LASTEXITCODE -eq 0)
    }

    # ---------------------------------------------------------------- 2. prerequis + acces
    Titre "2. Route publique et acces"
    $pub = Appel "/health" ""
    if ($pub.Code -eq 0) {
        Write-Host "  L'API ne repond pas sur $base : lance  uv run task dev-lan  dans un autre terminal." -ForegroundColor Red
        exit 2
    }
    Check "/health public : 200 et {status: ok}" ($pub.Code -eq 200 -and $pub.Json.status -eq "ok") "code $($pub.Code)"
    $sans = Appel "/v1/health" ""
    Check "/v1/health sans cle : 401 unauthorized" ($sans.Code -eq 401 -and $sans.Json.error.code -eq "unauthorized") "code $($sans.Code)"
    $faux = Appel "/v1/health" ("x" * 40)
    Check "/v1/health fausse cle : 401" ($faux.Code -eq 401) "code $($faux.Code)"

    # ---------------------------------------------------------------- 3. etat de depart
    Titre "3. Etat de depart"
    if ($Nettoyer) {
        Py $pyNettoyer @()
        Write-Host "  Anciens echecs supprimes de la base de dev." -ForegroundColor Yellow
    }
    $r = Attendre-Statut "ok" 30
    Check "etat de depart : 200 et status ok" ($r.Code -eq 200 -and $r.Json.status -eq "ok") `
        "status=$($r.Json.status), jobs abandonnes=$($r.Json.queue.jobs.abandoned), webhooks abandonnes=$($r.Json.queue.webhooks.abandoned) -> relance avec -Nettoyer"
    if ($r.Json) {
        $j = $r.Json
        Check "version presente" ([bool]$j.version)
        # texte brut : Windows PowerShell 5.1 convertit les dates JSON et perd le decalage horaire
        Check "checked_at a l'heure de Paris (+01:00 ou +02:00)" ($r.Texte -match '"checked_at"\s*:\s*"[^"]*[+]0[12]:00"')
        foreach ($n in "database", "livekit", "storage") {
            $c = $j.checks.$n
            Check "$n : ok, latence mesuree, pas de detail" ($c.status -eq "ok" -and $c.latency_ms -ge 0 -and $null -eq $c.detail)
        }
        Check "file de taches lue (compteurs presents)" ($null -ne $j.queue.jobs.abandoned -and $null -ne $j.queue.webhooks.abandoned)
    }
    $abandonnesAvant = 0
    if ($r.Json -and $r.Json.queue) { $abandonnesAvant = [int]$r.Json.queue.jobs.abandoned }

    # ---------------------------------------------------------------- 4 a 6. coupures reelles
    if ($SansDocker) {
        Write-Host "`n(coupures Docker sautees : -SansDocker)" -ForegroundColor Yellow
    } else {
        Titre "4. S3 coupe pour de vrai"
        Docker-Compose stop s3
        $r = Appel "/v1/health"
        Check "S3 coupe : 503 et degraded" ($r.Code -eq 503 -and $r.Json.status -eq "degraded") "code $($r.Code) status $($r.Json.status)"
        Check "storage = down, detail 'injoignable' ou 'delai depasse'" `
            ($r.Json.checks.storage.status -eq "down" -and $r.Json.checks.storage.detail -match '^(injoignable|d.lai d.pass.)$') $r.Json.checks.storage.detail
        Check "livekit et base restent ok" ($r.Json.checks.livekit.status -eq "ok" -and $r.Json.checks.database.status -eq "ok")
        $pub = Appel "/health" ""
        Check "/health public reste 200 (sonde Docker / Coolify)" ($pub.Code -eq 200)
        Docker-Compose start s3
        Write-Host "  S3 rallume : attente du retour a 'ok' (jusqu'a 120 s)..." -ForegroundColor DarkGray
        $r = Attendre-Statut "ok" 120
        Check "S3 rallume : retour a 200 et ok" ($r.Code -eq 200 -and $r.Json.status -eq "ok") "status $($r.Json.status), storage $($r.Json.checks.storage.status)"

        Titre "5. LiveKit coupe pour de vrai"
        Docker-Compose stop livekit
        $r = Appel "/v1/health"
        Check "LiveKit coupe : 503 et degraded" ($r.Code -eq 503 -and $r.Json.status -eq "degraded") "code $($r.Code) status $($r.Json.status)"
        Check "livekit = down, detail 'injoignable' ou 'delai depasse'" `
            ($r.Json.checks.livekit.status -eq "down" -and $r.Json.checks.livekit.detail -match '^(injoignable|d.lai d.pass.)$') $r.Json.checks.livekit.detail
        Check "S3 et base restent ok" ($r.Json.checks.storage.status -eq "ok" -and $r.Json.checks.database.status -eq "ok")
        Docker-Compose start livekit
        $r = Attendre-Statut "ok" 120
        Check "LiveKit rallume : retour a 200 et ok" ($r.Code -eq 200 -and $r.Json.status -eq "ok") "status $($r.Json.status), livekit $($r.Json.checks.livekit.status)"

        Titre "6. S3 et LiveKit coupes ensemble"
        Docker-Compose stop s3 livekit
        $chrono = [System.Diagnostics.Stopwatch]::StartNew()
        $r = Appel "/v1/health"
        $chrono.Stop()
        $secondes = [math]::Round($chrono.Elapsed.TotalSeconds, 2)
        Check "la route repond en moins de 5 s ($secondes s) : jamais bloquee" ($secondes -lt 5) "$secondes s"
        Check "503 et degraded, les deux dependances down" `
            ($r.Code -eq 503 -and $r.Json.status -eq "degraded" -and $r.Json.checks.livekit.status -eq "down" -and $r.Json.checks.storage.status -eq "down")
        Docker-Compose start s3 livekit
        $r = Attendre-Statut "ok" 120
        Check "tout rallume : retour a 200 et ok" ($r.Code -eq 200 -and $r.Json.status -eq "ok") "status $($r.Json.status)"
    }

    # ---------------------------------------------------------------- 7. file de taches
    Titre "7. File de taches en souffrance"
    Py $pyInserer @($idTest)
    $r = Appel "/v1/health"
    Check "tache abandonnee : 503 et degraded" ($r.Code -eq 503 -and $r.Json.status -eq "degraded") "code $($r.Code) status $($r.Json.status)"
    Check "queue.jobs.abandoned a augmente de 1" ([int]$r.Json.queue.jobs.abandoned -eq ($abandonnesAvant + 1)) "vu $($r.Json.queue.jobs.abandoned), attendu $($abandonnesAvant + 1)"
    Check "les dependances restent ok" ($r.Json.checks.database.status -eq "ok" -and $r.Json.checks.livekit.status -eq "ok" -and $r.Json.checks.storage.status -eq "ok")
    Py $pyRetirer @($idTest)
    $r = Appel "/v1/health"
    Check "tache retiree : retour a 200 et ok" ($r.Code -eq 200 -and $r.Json.status -eq "ok") "status $($r.Json.status)"

    # ---------------------------------------------------------------- 8. fuites
    Titre "8. Rien de sensible dans les reponses"
    $tout = $script:corps -join "`n"
    foreach ($interdit in @($key, $bucket, "localhost", "127.0.0.1", ":9000", ":7880", "Traceback", "EndpointConnectionError")) {
        if ($interdit) { Check "aucune reponse ne contient '$($interdit.Substring(0, [math]::Min(6, $interdit.Length)))...'" (-not $tout.Contains($interdit)) }
    }
}
finally {
    # Quoi qu'il arrive : on remet l'environnement en etat.
    if (-not $SansDocker) { Docker-Compose start s3 livekit }
    Py $pyRetirer @($idTest) 2>$null
}

Write-Host ""
if ($script:ko -eq 0) {
    Write-Host "RESULTAT : $($script:ok) verifications OK, 0 echec  ->  etape 17b VALIDE cote technique." -ForegroundColor Green
    exit 0
} else {
    Write-Host "RESULTAT : $($script:ko) ECHEC(S) sur $($script:ok + $script:ko) verifications." -ForegroundColor Red
    exit 1
}
