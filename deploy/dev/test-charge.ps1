# Test de charge (etape 20) : N salles x P participants avec `lk perf load-test`, releves
# docker stats dans un CSV, enregistrement optionnel de quelques salles, puis rapport.
# Prerequis : `lk` installe (winget install LiveKit.LiveKitCLI), docker-dev.ps1 up ;
# avec -Recorded : l'API lancee (uv run task dev-lan).
# Un PC de developpement ne tient PAS 300 connexions + le serveur : lance de preference ce script
# depuis UNE AUTRE machine (-Url ws://<ip-du-serveur>:7880).
#
#   .\deploy\dev\test-charge.ps1 -Rooms 2 -Participants 10 -Minutes 2     # essai
#   .\deploy\dev\test-charge.ps1 -Recorded 3                              # 10 x 30, 30 min, 3 enregistrees
#   .\deploy\dev\test-charge.ps1 -Rooms 15 -Recorded 4                    # x 1,5 : 450 connexions
#   .\deploy\dev\test-charge.ps1 -Rooms 1 -Minutes 480 -Recorded 1 -SampleSeconds 60   # 8 h, 1 reunion
param(
    [int]$Rooms = 10,
    [int]$Participants = 30,
    [int]$Minutes = 30,
    [int]$VideoPub = 4,
    [int]$AudioPub = 6,
    [int]$Recorded = 0,
    [int]$SampleSeconds = 10,
    [string]$Url = "ws://localhost:7880",
    [string]$ApiKey = "",
    [string]$ApiSecret = ""
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
if (-not (Get-Command lk -ErrorAction SilentlyContinue)) { throw "lk introuvable : winget install LiveKit.LiveKitCLI" }

# Cles : celles que le SERVEUR connait (config du conteneur LiveKit de dev), sinon .env.development.
if (-not $ApiKey -or -not $ApiSecret) {
    $yaml = docker exec eduvia-visio-dev-livekit-1 cat /etc/livekit.yaml 2>$null
    $paire = $yaml | Select-String -Pattern '^\s*"?([^":\s]+)"?\s*:\s*"([^"]+)"\s*$' | Where-Object { $_.Line -notmatch "api_key|api_secret|secret:|key:" } | Select-Object -First 1
    if ($paire) { $ApiKey = $paire.Matches[0].Groups[1].Value; $ApiSecret = $paire.Matches[0].Groups[2].Value }
}
if (-not $ApiKey) { $ApiKey = (dotenvx get LIVEKIT_API_KEY -f .env.development).Trim() }
if (-not $ApiSecret) { $ApiSecret = (dotenvx get LIVEKIT_API_SECRET -f .env.development).Trim() }
if (-not $ApiKey -or -not $ApiSecret) { throw "Cles LiveKit introuvables : passe -ApiKey et -ApiSecret" }
Write-Host "Cle LiveKit utilisee : $ApiKey" -ForegroundColor DarkGray

# lk : les video-publishers publient AUSSI un micro, donc publieurs = max(video, audio) (et non leur somme).
# Sans cette correction, une "salle de 30" n'en comptait que 26 (voir DECISIONS.md, point 249).
$pubs = [Math]::Max($VideoPub, $AudioPub)
$subs = $Participants - $pubs
if ($subs -lt 0) { throw "max(VideoPub, AudioPub) depasse Participants" }
$dir = "data/charge/" + (Get-Date -Format "yyyyMMdd-HHmm")
New-Item -ItemType Directory -Force $dir | Out-Null
$dir = (Resolve-Path $dir).Path   # chemin absolu : le job d'echantillonnage a un autre dossier courant
$csv = Join-Path $dir "stats.csv"
"epoch;name;cpu;ram;net;disk" | Out-File $csv -Encoding utf8

# Echantillonneur : un releve docker stats toutes les $SampleSeconds s, jusqu'a la fin du test.
$sampler = Start-Job -ArgumentList $csv, $SampleSeconds -ScriptBlock {
    param($csv, $every)
    while ($true) {
        $t = [int][double]::Parse((Get-Date -UFormat %s))
        docker stats --no-stream --format "{{.Name}};{{.CPUPerc}};{{.MemUsage}};{{.NetIO}};{{.BlockIO}}" |
            ForEach-Object { "$t;$_" } | Out-File $csv -Append -Encoding utf8
        Start-Sleep $every
    }
}

Write-Host "$Rooms salles x $Participants participants ($VideoPub video, $AudioPub audio, $subs auditeurs), $Minutes min" -ForegroundColor Green
$debutTest = Get-Date
$procs = @()
for ($i = 1; $i -le $Rooms; $i++) {
    $log = Join-Path $dir "room-$i.log"
    $procs += Start-Process lk -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err" -ArgumentList @(
        "perf", "load-test", "--url", $Url, "--api-key", $ApiKey, "--api-secret", $ApiSecret,
        "--room", "charge-$i", "--video-publishers", $VideoPub, "--audio-publishers", $AudioPub,
        "--subscribers", $subs, "--duration", "${Minutes}m", "--layout", "3x3",
        "--simulate-speakers", "--num-per-second", "5")
    Start-Sleep 2   # etale les connexions : les salles ne se ruent pas dans la meme seconde
}

# Controle precoce : si lk ne connecte personne (cles fausses...), inutile d'attendre la fin.
Start-Sleep 20
$echec = Get-ChildItem $dir -Filter "room-*.log*" | Select-String -Pattern "could not connect|unauthorized" | Select-Object -First 3
if ($echec) {
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Stop-Job $sampler; Remove-Job $sampler
    Write-Host "ECHEC : lk n'arrive pas a se connecter :" -ForegroundColor Red
    $echec | ForEach-Object { Write-Host "  $($_.Line)" }
    throw "Test annule"
}

$rec = $null
if ($Recorded -gt 0) {
    # Les salles sont pleines apres ~30 s : on demarre l'enregistrement (meme mise en page qu'en prod).
    Start-Sleep 20
    $secondes = [Math]::Max(60, $Minutes * 60 - 90)
    $rec = Start-Process uv -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $dir "record.log") -RedirectStandardError (Join-Path $dir "record.log.err") -ArgumentList @(
        "run", "task", "load-record", "--", "--rooms", $Recorded, "--seconds", $secondes,
        "--api-key", $ApiKey, "--api-secret", $ApiSecret)
}

$procs | Wait-Process
if ($rec) { $rec | Wait-Process; Get-Content (Join-Path $dir "record.log") }
Stop-Job $sampler; Remove-Job $sampler

if (((Get-Date) - $debutTest).TotalMinutes -lt ($Minutes * 0.8)) {
    Write-Host "ATTENTION : fin bien avant $Minutes min, lk a probablement echoue. Dernieres lignes de room-1 :" -ForegroundColor Red
    Get-Content (Join-Path $dir "room-1.log"), (Join-Path $dir "room-1.log.err") -Tail 15 -ErrorAction SilentlyContinue
}
Write-Host "Fin. Releves : $csv" -ForegroundColor Green

# Lignes d'erreur ou de deconnexion des logs de lk (les plus frequentes d'abord).
$lignes = Get-ChildItem $dir -Filter "room-*.log*" | Select-String -Pattern "error|failed|disconnect|could not" |
    ForEach-Object { $_.Line.Trim() } | Group-Object | Sort-Object Count -Descending | Select-Object -First 5
foreach ($l in $lignes) { Write-Host "  $($l.Count) x $($l.Name)" -ForegroundColor Yellow }

uv run task load-report -- $csv --participants ($Rooms * $Participants)
