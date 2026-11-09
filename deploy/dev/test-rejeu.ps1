# Test AUTOMATIQUE de l'etape 17c (jobs en echec visibles et rejouables) : tout est enchaine, rien a taper.
# A lancer depuis la racine du depot, avec Docker de dev (docker-dev.ps1 up) et l'API
# (uv run task dev-lan) deja lances. Le recepteur de webhooks (webhook-receiver.py) est facultatif.
#
#   .\deploy\dev\test-rejeu.ps1                 # tout : pytest + vraie panne LiveKit + rejeu + webhook
#   .\deploy\dev\test-rejeu.ps1 -SansPytest     # saute les tests automatiques (plus rapide)
#   .\deploy\dev\test-rejeu.ps1 -SansDocker     # ne coupe pas LiveKit : l'echec est injecte en base
#
# Ce que fait le script (tout est limite a UNE reunion de test creee par le script : les anciens
# echecs de ta base de dev ne genent pas, et ne sont pas touches) :
#   1. pytest : tests/integration/test_replay.py
#   2. cree une reunion de test par l'API
#   3. coupe LiveKit pour de vrai, pose une tache de fermeture de salle : elle ECHOUE vraiment
#      -> jobs-list la montre avec son erreur et la commande de rejeu
#   4. avance jusqu'a l'abandon (on ne peut pas attendre 1 h 13 de relances), rallume LiveKit
#   5. jobs-retry --all : la tache repart et se TERMINE ; rien n'est cree en double
#   6. un second jobs-retry est refuse / sans effet
#   7. webhook abandonne : jobs-list le montre, jobs-retry le remet en file, le planificateur le reprend
#   8. echec definitif (enregistrement sans tache a rejouer) : liste, mais le rejeu est refuse
#   9. rien de sensible dans ce qu'affichent les commandes
# LiveKit est TOUJOURS rallume a la fin, et les lignes injectees sont retirees, meme si un test echoue.
# Code de sortie 0 = tout vert.

param(
    [switch]$SansPytest,
    [switch]$SansDocker
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$base = "http://localhost:8000"
$compose = Join-Path $PSScriptRoot "..\docker-compose.dev.yml"
$key = (dotenvx get API_KEY -f .env.development)
$secretWebhook = (dotenvx get WEBHOOK_SECRET -f .env.development)
$urlWebhook = (dotenvx get WEBHOOK_URL -f .env.development)
$bucket = (dotenvx get S3_BUCKET -f .env.development)
$dbUrl = (dotenvx get DATABASE_URL -f .env.development)
$dbPath = $dbUrl -replace '^sqlite:///', ''
$suffixe = Get-Date -Format "HHmmss"
$idJob = "job_test17c$suffixe"
$idWebhook = "evt_test17c$suffixe"
$idArtefact = "art_test17c$suffixe"
$script:ok = 0
$script:ko = 0
$script:sorties = @()   # tout ce que les commandes ont affiche : on y cherche des fuites a la fin
$script:reunion = $null

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

function Docker-Compose { docker compose -f $compose @args 2>&1 | Out-Null }

function Py([string]$code, [string[]]$arguments) {
    $code | uv run python - $dbPath @arguments
}

# Lance une commande du depot (jobs-list / jobs-retry) : rend le code de sortie et le texte.
function Commande([string[]]$arguments) {
    $texte = (uv run task @arguments 2>&1 | Out-String)
    $code = $LASTEXITCODE
    $script:sorties += $texte
    return [pscustomobject]@{ Code = $code; Texte = $texte }
}

function Etat-Livekit {
    $a = @("-s", "-m", "15", "-H", "Authorization: Bearer $key", "$base/v1/health")
    $texte = (curl.exe @a | Out-String)
    try { return ($texte | ConvertFrom-Json).checks.livekit.status } catch { return "?" }
}

function Attendre-Livekit([int]$max = 120) {
    $debut = Get-Date
    do {
        if ((Etat-Livekit) -eq "ok") { return $true }
        Start-Sleep -Seconds 2
    } while (((Get-Date) - $debut).TotalSeconds -lt $max)
    return $false
}

# --- petites requetes SQL (meme base que l'API) -----------------------------------------------

$pyInsererJob = @'
import sqlite3, sys, json, datetime as d
c = sqlite3.connect(sys.argv[1])
n = d.datetime.now(d.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
c.execute("delete from jobs where id = ?", (sys.argv[2],))
c.execute("insert into jobs(id,kind,meeting_id,payload,status,run_at,attempts,last_error,created_at,updated_at) values(?, 'close_room', ?, ?, ?, ?, ?, NULL, ?, ?)",
          (sys.argv[2], sys.argv[3], json.dumps({"room": "test17c"}), sys.argv[4], n, int(sys.argv[5]), n, n))
c.commit()
'@

$pyLireJob = @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute("select status, attempts, coalesce(last_error, '') from jobs where id = ?", (sys.argv[2],)).fetchone()
print("|".join(str(x) for x in r) if r else "absent|0|")
'@

$pyCompterJobs = @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("select count(*) from jobs where meeting_id = ? and kind = 'close_room'", (sys.argv[2],)).fetchone()[0])
'@

$pyForcerAbandon = @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("update jobs set status = 'abandoned', attempts = 6 where id = ?", (sys.argv[2],))
c.commit()
'@

$pyInsererWebhook = @'
import sqlite3, sys, json, datetime as d
c = sqlite3.connect(sys.argv[1])
now = d.datetime.now(d.timezone.utc)
n = now.strftime("%Y-%m-%d %H:%M:%S.%f")
corps = {"id": sys.argv[3], "type": "meeting.ended", "created_at": now.isoformat(),
         "meeting": {"id": sys.argv[2], "title": "Essai 17c", "starts_at": now.isoformat(), "ends_at": now.isoformat(),
                     "metadata": {"eduvia_session_id": 17}},
         "data": {"reason": "test 17c"}}
c.execute("delete from webhook_deliveries where id = ?", (sys.argv[3],))
c.execute("insert into webhook_deliveries(id,meeting_id,event_type,payload,status,attempts,next_attempt_at,created_at,delivered_at,last_error) values(?, ?, 'meeting.ended', ?, 'abandoned', 6, ?, ?, NULL, 'test 17c')",
          (sys.argv[3], sys.argv[2], json.dumps(corps), n, n))
c.commit()
'@

$pyLireWebhook = @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute("select status, attempts from webhook_deliveries where id = ?", (sys.argv[2],)).fetchone()
n = c.execute("select count(*) from webhook_attempts where delivery_id = ?", (sys.argv[2],)).fetchone()[0]
print(("|".join(str(x) for x in r) if r else "absent|0") + "|" + str(n))
'@

$pyInsererArtefact = @'
import sqlite3, sys, datetime as d
c = sqlite3.connect(sys.argv[1])
n = d.datetime.now(d.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
c.execute("delete from artifacts where id = ?", (sys.argv[3],))
c.execute("insert into artifacts(id,meeting_id,kind,status,storage_key,egress_id,size_bytes,error,created_at) values(?, ?, 'recording', 'failed', NULL, NULL, NULL, 'Egress a echoue (test 17c)', ?)",
          (sys.argv[3], sys.argv[2], n))
c.commit()
'@

$pyNettoyer = @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("delete from jobs where id = ?", (sys.argv[2],))
c.execute("delete from webhook_attempts where delivery_id = ?", (sys.argv[3],))
c.execute("delete from webhook_deliveries where id = ?", (sys.argv[3],))
c.execute("delete from artifacts where id = ?", (sys.argv[4],))
c.commit()
'@

function Etat-Job {
    $s = ("$(Py $pyLireJob @($idJob))").Trim() -split '\|'
    return [pscustomobject]@{ Statut = $s[0]; Essais = [int]$s[1]; Erreur = $s[2] }
}

function Attendre-Job([string]$statut, [int]$max = 40) {
    $debut = Get-Date
    do {
        $e = Etat-Job
        if ($e.Statut -eq $statut) { return $e }
        Start-Sleep -Seconds 1
    } while (((Get-Date) - $debut).TotalSeconds -lt $max)
    return $e
}

try {
    # ---------------------------------------------------------------- 1. pytest
    if (-not $SansPytest) {
        Titre "1. Tests automatiques (pytest)"
        uv run pytest tests/integration/test_replay.py -q
        Check "pytest : tous les tests de rejeu passent" ($LASTEXITCODE -eq 0)
    }

    # ---------------------------------------------------------------- 2. reunion de test
    Titre "2. Reunion de test"
    $pub = (curl.exe -s -m 10 -o NUL -w "%{http_code}" "$base/health")
    if ($pub -ne "200") {
        Write-Host "  L'API ne repond pas sur $base : lance  uv run task dev-lan  dans un autre terminal." -ForegroundColor Red
        exit 2
    }
    $debut = Get-Date
    $corps = @{
        title        = "Test automatique 17c"
        starts_at    = $debut.ToString("yyyy-MM-ddTHH:mm:sszzz")
        ends_at      = $debut.AddMinutes(30).ToString("yyyy-MM-ddTHH:mm:sszzz")
        waiting_room = $false
        recording    = $false
        transcription = $false
        organizers   = @(@{ display_name = "Marie Durand"; external_id = "staff_42" })
        metadata     = @{ eduvia_session_id = 17 }
    } | ConvertTo-Json -Depth 6
    try {
        $m = Invoke-RestMethod -Method Post "$base/v1/meetings" -Headers @{ Authorization = "Bearer $key" } `
            -ContentType "application/json" -Body $corps
        $script:reunion = $m.id
    } catch {
        Write-Host "  Creation de la reunion impossible : $($_.Exception.Message)" -ForegroundColor Red
        exit 2
    }
    Check "reunion creee ($($script:reunion))" ([bool]$script:reunion)
    $r = Commande @("jobs-list", $script:reunion)
    Check "jobs-list sur cette reunion : code 0 et 'Rien en echec'" ($r.Code -eq 0 -and $r.Texte -match "Rien en .chec") "code $($r.Code)"

    # ---------------------------------------------------------------- 3. vraie panne
    Titre "3. Une vraie panne : LiveKit coupe, la tache de fermeture de salle echoue"
    if ($SansDocker) {
        Write-Host "  (-SansDocker : l'echec est injecte en base, deja abandonne, au lieu d'etre provoque)" -ForegroundColor Yellow
        Py $pyInsererJob @($idJob, $script:reunion, "abandoned", "6")
    } else {
        Docker-Compose stop livekit
        Py $pyInsererJob @($idJob, $script:reunion, "pending", "0")
        $e = Attendre-Job "failed" 40
        Check "la tache echoue vraiment (statut failed, 1 essai au moins)" ($e.Statut -eq "failed" -and $e.Essais -ge 1) "statut $($e.Statut), essais $($e.Essais)"
        Check "l'erreur est enregistree (serveur de medias injoignable)" ([bool]$e.Erreur) "aucune erreur en base"
    }
    $r = Commande @("jobs-list", $script:reunion)
    Check "jobs-list : code 1 (il y a un echec)" ($r.Code -eq 1) "code $($r.Code)"
    Check "jobs-list montre la tache, 'close_room' et son etat en echec" ($r.Texte.Contains($idJob) -and $r.Texte.Contains("close_room") -and $r.Texte -match "(en .chec|abandonn)")
    Check "jobs-list donne la commande de rejeu" ($r.Texte.Contains("jobs-retry $idJob"))
    Check "jobs-list donne l'erreur" ($r.Texte -match "erreur :")
    $autre = Commande @("jobs-list", "mtg_inexistant17c")
    Check "le filtre par reunion marche : autre reunion -> code 0" ($autre.Code -eq 0)

    # ---------------------------------------------------------------- 4. abandon + retour de LiveKit
    Titre "4. Abandon (relances epuisees), puis LiveKit revient"
    Py $pyForcerAbandon @($idJob)
    $e = Etat-Job
    Check "la tache est abandonnee (6 essais)" ($e.Statut -eq "abandoned" -and $e.Essais -eq 6) "statut $($e.Statut), essais $($e.Essais)"
    $r = Commande @("jobs-list", $script:reunion)
    Check "jobs-list : 'abandonn' et code 1" ($r.Code -eq 1 -and $r.Texte -match "abandonn")
    if (-not $SansDocker) {
        Docker-Compose start livekit
        Write-Host "  LiveKit rallume : attente du retour (jusqu'a 120 s)..." -ForegroundColor DarkGray
        Check "LiveKit est revenu (vu par /v1/health)" (Attendre-Livekit 120)
    }

    # ---------------------------------------------------------------- 5. rejeu
    Titre "5. Rejeu : la tache repart et se termine"
    $r = Commande @("jobs-retry", "--all", $script:reunion)
    Check "jobs-retry --all : code 0" ($r.Code -eq 0) "code $($r.Code) : $($r.Texte)"
    Check "la sortie dit 'remis en file' pour cette tache" ($r.Texte.Contains($idJob) -and $r.Texte -match "remis en file")
    $e = Attendre-Job "done" 40
    Check "le planificateur la reprend et elle se TERMINE (done)" ($e.Statut -eq "done") "statut $($e.Statut), essais $($e.Essais), erreur $($e.Erreur)"
    $n = ("$(Py $pyCompterJobs @($script:reunion))").Trim()
    Check "aucune tache en double (une seule close_room pour la reunion)" ($n -eq "1") "$n taches"
    $r = Commande @("jobs-list", $script:reunion)
    Check "jobs-list : plus rien en echec (code 0)" ($r.Code -eq 0) "code $($r.Code)"

    # ---------------------------------------------------------------- 6. pas de doublon
    Titre "6. Rejouer une tache deja terminee"
    $r = Commande @("jobs-retry", $idJob)
    Check "refuse (code 1) : la tache est deja terminee" ($r.Code -eq 1 -and $r.Texte -match "refus") "code $($r.Code)"
    $r = Commande @("jobs-retry", "--all", $script:reunion)
    Check "jobs-retry --all : rien d'abandonne, code 0" ($r.Code -eq 0 -and $r.Texte -match "Rien d'abandonn")
    $n = ("$(Py $pyCompterJobs @($script:reunion))").Trim()
    Check "toujours une seule tache" ($n -eq "1") "$n taches"

    # ---------------------------------------------------------------- 7. webhook
    Titre "7. Webhook abandonne : visible, remis en file, repris par le planificateur"
    Py $pyInsererWebhook @($script:reunion, $idWebhook)
    $r = Commande @("jobs-list", $script:reunion)
    Check "jobs-list montre le webhook abandonne" ($r.Texte.Contains($idWebhook) -and $r.Texte.Contains("meeting.ended") -and $r.Texte -match "abandonn")
    $r = Commande @("jobs-retry", $idWebhook)
    Check "jobs-retry <evt_...> : code 0 et 'remis en file'" ($r.Code -eq 0 -and $r.Texte -match "remis en file") "code $($r.Code) : $($r.Texte)"
    $debut = Get-Date
    do {
        $w = ("$(Py $pyLireWebhook @($idWebhook))").Trim() -split '\|'
        if ($w[0] -eq "delivered" -or [int]$w[2] -ge 1) { break }
        Start-Sleep -Seconds 1
    } while (((Get-Date) - $debut).TotalSeconds -lt 30)
    Check "le planificateur a tente la livraison (journal des tentatives rempli)" ([int]$w[2] -ge 1) "statut $($w[0]), tentatives $($w[2])"
    if ($w[0] -eq "delivered") {
        Write-Host "  [INFO]  livre : le recepteur de webhooks repond." -ForegroundColor DarkGray
        $r = Commande @("jobs-retry", $idWebhook)
        Check "webhook deja livre : rejeu refuse (jamais de second envoi)" ($r.Code -eq 1 -and $r.Texte -match "refus") "code $($r.Code)"
    } else {
        Write-Host "  [INFO]  non livre (recepteur absent ?) : lance deploy\dev\webhook-receiver.py pour voir la livraison." -ForegroundColor Yellow
        $r = Commande @("jobs-retry", $idWebhook)
        Check "rejeu repete : accepte ou 'deja en attente', sans erreur" ($r.Code -eq 0) "code $($r.Code) : $($r.Texte)"
    }

    # ---------------------------------------------------------------- 8. echec definitif
    Titre "8. Echec definitif : liste mais non rejouable"
    Py $pyInsererArtefact @($script:reunion, $idArtefact)
    $r = Commande @("jobs-list", $script:reunion)
    Check "jobs-list montre l'enregistrement en echec" ($r.Texte.Contains($idArtefact) -and $r.Texte.Contains("Egress a echoue"))
    Check "jobs-list explique : echec definitif, le direct ne se rejoue pas" ($r.Texte -match "d.finitif")
    $r = Commande @("jobs-retry", $idArtefact)
    Check "jobs-retry <art_...> : refuse (code 1), motif donne" ($r.Code -eq 1 -and $r.Texte -match "refus" -and $r.Texte -match "d.finitif") "code $($r.Code)"
    $r = Commande @("jobs-retry", "art_inconnu17c")
    Check "identifiant inconnu : 'introuvable' (code 1)" ($r.Code -eq 1 -and $r.Texte -match "introuvable")
    $r = Commande @("jobs-retry")
    Check "sans argument : mode d'emploi et code 2" ($r.Code -eq 2 -and $r.Texte -match "usage")

    # ---------------------------------------------------------------- 9. fuites
    Titre "9. Rien de sensible dans ce que les commandes affichent"
    $tout = $script:sorties -join "`n"
    foreach ($interdit in @($key, $secretWebhook, $urlWebhook, $bucket, "Traceback", "eduvia_session_id")) {
        if ($interdit) { Check "aucune sortie ne contient '$($interdit.Substring(0, [math]::Min(6, $interdit.Length)))...'" (-not $tout.Contains($interdit)) }
    }
}
finally {
    # Quoi qu'il arrive : on remet l'environnement en etat.
    if (-not $SansDocker) { Docker-Compose start livekit }
    Py $pyNettoyer @($idJob, $idWebhook, $idArtefact) 2>$null
    if ($script:reunion) {
        try {
            Invoke-RestMethod -Method Post "$base/v1/meetings/$($script:reunion)/end" -Headers @{ Authorization = "Bearer $key" } | Out-Null
        } catch { }
    }
}

Write-Host ""
if ($script:ko -eq 0) {
    Write-Host "RESULTAT : $($script:ok) verifications OK, 0 echec  ->  etape 17c VALIDE cote technique." -ForegroundColor Green
    exit 0
} else {
    Write-Host "RESULTAT : $($script:ko) ECHEC(S) sur $($script:ok + $script:ko) verifications." -ForegroundColor Red
    exit 1
}
