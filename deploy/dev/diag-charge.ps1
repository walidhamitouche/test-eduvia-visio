# Diagnostic du test de charge (etape 20, scenario 9) : trouver POURQUOI 10 x 30 ne tient pas.
#
# Principe : a chaque palier (2, 4, 6, 8, 10 salles de 30), on mesure EN MEME TEMPS
#   - le CPU de l'HOTE (tout le PC),
#   - le CPU du generateur de charge (lk), du serveur LiveKit, de Docker/WSL (vmmem),
#   - le nombre de participants REELLEMENT connectes dans LiveKit (interroge l'API),
#   - les erreurs et panics des logs de lk.
# Puis un verdict par palier : hote sature / perte sans saturation / OK.
#
# Usage (depuis la racine du depot, PowerShell lance EN ADMINISTRATEUR pour lire vmmem) :
#   .\deploy\dev\diag-charge.ps1 -Mode Preflight
#   .\deploy\dev\diag-charge.ps1                                  # paliers 2,4,6,8,10 sur LiveKit Docker
#   .\deploy\dev\diag-charge.ps1 -Cible Natif                     # meme chose sur livekit-server.exe natif
#   .\deploy\dev\diag-charge.ps1 -Paliers 10 -Minutes 5           # un seul palier
#   .\deploy\dev\diag-charge.ps1 -Url ws://192.168.1.50:7880 -Cible Docker   # lk depuis un AUTRE PC
#
# Les fichiers sont ecrits dans data/diag/<date>/ (ignore par Git).
param(
    [ValidateSet("Preflight", "Paliers")][string]$Mode = "Paliers",
    [ValidateSet("Docker", "Natif")][string]$Cible = "Docker",
    [int[]]$Paliers = @(2, 4, 6, 8, 10),
    [int]$Participants = 30,
    [ValidateRange(2, 600)][int]$Minutes = 3,
    [int]$VideoPub = 4,
    [int]$AudioPub = 6,
    [int]$SampleSeconds = 5,
    [string]$Url = "ws://localhost:7880",
    [string]$Layout = "3x3",
    [string]$ApiKey = "",
    [string]$ApiSecret = "",
    [string]$Conteneur = "eduvia-visio-dev-livekit-1",
    [switch]$ContinuerApresEchec
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$ncpu = [int](Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$suivis = @("lk", "livekit-server", "vmmem", "vmmemWSL", "com.docker.backend", "wslhost", "MsMpEng")

function Moy($a) {
    $v = @($a | Where-Object { $_ -ne $null })
    if ($v.Count -eq 0) { return 0 }
    return [Math]::Round((($v | Measure-Object -Average).Average), 2)
}
function Maxi($a) {
    $v = @($a | Where-Object { $_ -ne $null })
    if ($v.Count -eq 0) { return 0 }
    return [Math]::Round((($v | Measure-Object -Maximum).Maximum), 2)
}

# ---------------------------------------------------------------- Preflight
function Show-Preflight {
    Write-Host "=== PREFLIGHT ===" -ForegroundColor Cyan
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $os = Get-CimInstance Win32_OperatingSystem
    $cs = Get-CimInstance Win32_ComputerSystem
    Write-Host ("CPU          : {0} ({1} coeurs physiques, {2} logiques)" -f $cpu.Name.Trim(), $cpu.NumberOfCores, $ncpu)
    Write-Host ("RAM          : {0} Go au total, {1} Go libres" -f [Math]::Round($cs.TotalPhysicalMemory / 1GB, 1), [Math]::Round($os.FreePhysicalMemory / 1MB, 1))
    $batt = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
    if ($batt) { Write-Host "Batterie     : PC portable detecte (branche le secteur : sur batterie le CPU est bride)" -ForegroundColor Yellow }
    Write-Host ("Plan energie : " + ((powercfg /getactivescheme) -join " "))
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { Write-Host "Droits       : PAS administrateur : le CPU de vmmem (Docker/WSL) ne sera pas lisible" -ForegroundColor Yellow }
    else { Write-Host "Droits       : administrateur (ok)" }

    Write-Host "--- Docker ---" -ForegroundColor Cyan
    try {
        $info = docker info --format "{{.NCPU}} CPU, {{.MemTotal}} octets" 2>$null
        Write-Host ("Docker voit  : " + $info + "  (compare au PC : c'est la limite pour LiveKit + Egress)")
    } catch { Write-Host "Docker       : injoignable" -ForegroundColor Yellow }
    $wsl = Join-Path $env:USERPROFILE ".wslconfig"
    if (Test-Path $wsl) { Write-Host ".wslconfig   :"; Get-Content $wsl | ForEach-Object { Write-Host "    $_" } }
    else { Write-Host ".wslconfig   : absent (WSL2 prend par defaut 50 % de la RAM et tous les CPU)" }

    Write-Host "--- Generateur lk ---" -ForegroundColor Cyan
    if (-not (Get-Command lk -ErrorAction SilentlyContinue)) { Write-Host "lk introuvable : winget install LiveKit.LiveKitCLI" -ForegroundColor Red; return }
    Write-Host ("Version lk   : " + ((lk --version 2>&1) -join " "))
    Write-Host "Options liees a la mise en page (ce que fait vraiment --layout) :"
    (lk perf load-test --help 2>&1) | Select-String -Pattern "layout|simulate|num-per-second|video-resolution" | ForEach-Object { Write-Host ("    " + $_.Line.Trim()) }
    $dejaLa = @(Get-Process lk -ErrorAction SilentlyContinue).Count
    if ($dejaLa -gt 0) { Write-Host "$dejaLa processus lk deja lances : arrete-les (Get-Process lk | Stop-Process)" -ForegroundColor Yellow }
    Write-Host "--- Serveur ---" -ForegroundColor Cyan
    $dk = docker ps --filter "name=$Conteneur" --format "{{.Names}} {{.Status}}" 2>$null
    $nat = @(Get-Process livekit-server -ErrorAction SilentlyContinue).Count
    Write-Host ("LiveKit Docker : " + $(if ($dk) { $dk } else { "arrete" }))
    Write-Host ("LiveKit natif  : " + $(if ($nat -gt 0) { "en cours" } else { "arrete" }))
    if ($dk -and $nat -gt 0) { Write-Host "Les DEUX tournent : arrete-en un (meme port 7880)" -ForegroundColor Red }
}

if ($Mode -eq "Preflight") { Show-Preflight; return }

# ---------------------------------------------------------------- Preparation
if (-not (Get-Command lk -ErrorAction SilentlyContinue)) { throw "lk introuvable : winget install LiveKit.LiveKitCLI" }
if (-not (Test-Path "deploy/dev/diag_rooms.py")) { throw "Lance ce script depuis la racine du depot (deploy/dev/diag_rooms.py introuvable)" }
if (@(Get-Process lk -ErrorAction SilentlyContinue).Count -gt 0) { throw "Des processus lk tournent deja : Get-Process lk | Stop-Process" }

if (-not $ApiKey -or -not $ApiSecret) {
    if ($Cible -eq "Docker") {
        $yaml = docker exec $Conteneur cat /etc/livekit.yaml 2>$null
        $paire = $yaml | Select-String -Pattern '^\s*"?([^":\s]+)"?\s*:\s*"([^"]+)"\s*$' | Where-Object { $_.Line -notmatch "api_key|api_secret|secret:|key:" } | Select-Object -First 1
        if ($paire) { $ApiKey = $paire.Matches[0].Groups[1].Value; $ApiSecret = $paire.Matches[0].Groups[2].Value }
    } else {
        # LiveKit natif lance par livekit-dev.ps1 : memes cles que .env.development.
        $ApiKey = (dotenvx get LIVEKIT_API_KEY -f .env.development).Trim()
        $ApiSecret = (dotenvx get LIVEKIT_API_SECRET -f .env.development).Trim()
    }
}
if (-not $ApiKey -or -not $ApiSecret) { throw "Cles LiveKit introuvables : passe -ApiKey et -ApiSecret" }
if ($Cible -eq "Docker" -and -not (docker ps --filter "name=$Conteneur" --format "{{.Names}}" 2>$null)) {
    if ($Url -match "localhost|127\.0\.0\.1") { throw "Le conteneur $Conteneur ne tourne pas : .\deploy\dev\docker-dev.ps1 up" }
}
if ($Cible -eq "Natif" -and @(Get-Process livekit-server -ErrorAction SilentlyContinue).Count -eq 0) {
    throw "livekit-server.exe ne tourne pas : arrete Docker (docker stop $Conteneur eduvia-visio-dev-egress-1) puis lance .\deploy\dev\livekit-dev.ps1 dans un autre terminal"
}

$subs = $Participants - $VideoPub - $AudioPub
if ($subs -lt 0) { throw "VideoPub + AudioPub depasse Participants" }
$dir = "data/diag/" + (Get-Date -Format "yyyyMMdd-HHmm") + "-" + $Cible
New-Item -ItemType Directory -Force $dir | Out-Null
$dir = (Resolve-Path $dir).Path
try { Start-Transcript -Path (Join-Path $dir "transcript.txt") | Out-Null } catch {}

Show-Preflight
Write-Host ""
Write-Host ("Cible {0} | {1} | paliers {2} | {3} x ({4} video, {5} audio, {6} auditeurs) | {7} min chacun" -f $Cible, $Url, ($Paliers -join ","), "N salles", $VideoPub, $AudioPub, $subs, $Minutes) -ForegroundColor Green

# ---------------------------------------------------------------- Mesures
function Get-CpuSeconds {
    $h = @{}
    foreach ($n in $suivis) {
        $t = 0.0
        foreach ($p in @(Get-Process -Name $n -ErrorAction SilentlyContinue)) {
            try { $t += $p.TotalProcessorTime.TotalSeconds } catch {}
        }
        $h[$n] = $t
    }
    return $h
}
function Get-HostCpu {
    try { return [double](Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'").PercentProcessorTime } catch { return 0 }
}
function Get-DockerCores {
    # docker stats : 100 % = un coeur. Renvoie des coeurs, ou $null si Docker ne repond pas.
    try {
        $l = docker stats --no-stream --format "{{.CPUPerc}}" $Conteneur 2>$null
        if (-not $l) { return $null }
        return [double]::Parse(($l.Trim() -replace "%", ""), [Globalization.CultureInfo]::InvariantCulture) / 100
    } catch { return $null }
}
function Get-Connectes([string]$prefix) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $json = uv run python deploy/dev/diag_rooms.py --url $Url --key $ApiKey --secret $ApiSecret --prefix $prefix 2>$null
        $o = ($json | Select-Object -Last 1) | ConvertFrom-Json
        if ($o.PSObject.Properties.Name -contains "participants") { return [int]$o.participants }
        return -1
    } catch { return -1 } finally { $ErrorActionPreference = $old }
}

function Invoke-Palier([int]$rooms) {
    $attendu = $rooms * $Participants
    $dirP = Join-Path $dir ("palier-{0:00}" -f $rooms)
    New-Item -ItemType Directory -Force $dirP | Out-Null
    $prefix = "diag-p$rooms-"
    Write-Host ""
    Write-Host ("=== PALIER {0} salles = {1} connexions attendues ===" -f $rooms, $attendu) -ForegroundColor Cyan

    $debut = Get-Date
    $procs = @()
    for ($i = 1; $i -le $rooms; $i++) {
        $log = Join-Path $dirP "room-$i.log"
        $procs += Start-Process lk -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError "$log.err" -ArgumentList @(
            "perf", "load-test", "--url", $Url, "--api-key", $ApiKey, "--api-secret", $ApiSecret,
            "--room", "$prefix$i", "--video-publishers", $VideoPub, "--audio-publishers", $AudioPub,
            "--subscribers", $subs, "--duration", "${Minutes}m", "--layout", $Layout,
            "--simulate-speakers", "--num-per-second", "5")
        Start-Sleep 2
    }

    $csv = Join-Path $dirP "samples.csv"
    "t;host_cpu_pct;ram_libre_mo;lk_coeurs;livekit_coeurs;vmmem_coeurs;dockerbackend_coeurs;defender_coeurs;connectes" | Out-File $csv -Encoding ascii
    $s = @{ host = @(); lk = @(); live = @(); vm = @(); ram = @() }
    $checks = @([int](45 + 2 * $rooms), [int][Math]::Max(60 + 2 * $rooms + 30, $Minutes * 60 - 10))
    $obs = @{}
    $prev = Get-Date
    $last = Get-CpuSeconds
    $limite = $debut.AddSeconds($Minutes * 60 + 120 + 2 * $rooms)

    while ((@($procs | Where-Object { -not $_.HasExited }).Count -gt 0) -and ((Get-Date) -lt $limite)) {
        Start-Sleep $SampleSeconds
        $now = Get-Date
        $dt = ($now - $prev).TotalSeconds
        $cur = Get-CpuSeconds
        $c = @{}
        foreach ($n in $suivis) { $c[$n] = [Math]::Max(0, ($cur[$n] - $last[$n]) / $dt) }
        $prev = $now; $last = $cur
        $hostCpu = Get-HostCpu
        $free = [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
        $live = $c["livekit-server"]
        if ($Cible -eq "Docker") { $dc = Get-DockerCores; if ($dc -ne $null) { $live = $dc } else { $live = $null } }
        $vm = $c["vmmem"] + $c["vmmemWSL"]
        $t = [int]($now - $debut).TotalSeconds
        $conn = ""
        foreach ($k in $checks) { if ($t -ge $k -and -not $obs.ContainsKey($k)) { $obs[$k] = Get-Connectes $prefix; $conn = $obs[$k] } }
        ("{0};{1};{2};{3:N2};{4};{5:N2};{6:N2};{7:N2};{8}" -f $t, $hostCpu, $free, $c["lk"], $(if ($live -ne $null) { "{0:N2}" -f $live } else { "NA" }), $vm, $c["com.docker.backend"], $c["MsMpEng"], $conn) | Out-File $csv -Append -Encoding ascii
        $s.host += $hostCpu; $s.ram += $free; $s.lk += $c["lk"]; if ($live -ne $null) { $s.live += $live }; $s.vm += $vm
        Write-Host ("  t={0,4}s  hote {1,3} %  lk {2,5:N1} coeurs  serveur {3,5}  connectes {4}" -f $t, $hostCpu, $c["lk"], $(if ($live -ne $null) { "{0:N1}" -f $live } else { "NA" }), $conn)
    }

    $ontQuitteTot = @($procs | Where-Object { $_.HasExited -and ($_.ExitTime - $debut).TotalMinutes -lt ($Minutes * 0.8) }).Count
    $procs | Where-Object { -not $_.HasExited } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 15

    $logs = @(Get-ChildItem $dirP -Filter "room-*.log*")
    $panics = @($logs | Select-String -Pattern "panic|nil pointer").Count
    $erreurs = @($logs | Select-String -Pattern "error|failed|disconnect|could not|timeout").Count
    $top = $logs | Select-String -Pattern "error|failed|disconnect|could not|timeout|panic" | ForEach-Object { $_.Line.Trim() } | Group-Object | Sort-Object Count -Descending | Select-Object -First 5
    $top | ForEach-Object { Write-Host ("    {0} x {1}" -f $_.Count, $_.Name) -ForegroundColor Yellow }
    $top | ForEach-Object { "{0} x {1}" -f $_.Count, $_.Name } | Out-File (Join-Path $dirP "erreurs.txt") -Encoding utf8
    Get-Content (Join-Path $dirP "room-1.log") -Tail 15 -ErrorAction SilentlyContinue | Out-File (Join-Path $dirP "room-1-fin.txt") -Encoding utf8

    $k1 = $checks[0]; $k2 = $checks[1]
    $c1 = $(if ($obs.ContainsKey($k1)) { $obs[$k1] } else { -1 })
    $c2 = $(if ($obs.ContainsKey($k2)) { $obs[$k2] } else { -1 })
    $perte = $(if ($c2 -ge 0) { [Math]::Round(100 * ($attendu - $c2) / $attendu, 1) } else { -1 })
    $hostMax = Maxi $s.host
    $ramMin = $(if (@($s.ram).Count -gt 0) { [int](($s.ram | Measure-Object -Minimum).Minimum) } else { 0 })
    $verdict = ""
    if ($c2 -lt 0) { $verdict = "MESURE IMPOSSIBLE (API LiveKit injoignable : voir erreurs)" }
    elseif ($perte -le 2 -and $panics -eq 0 -and $ontQuitteTot -eq 0) { $verdict = "OK : tient" }
    elseif ($ramMin -lt 600) { $verdict = "RAM DU PC EPUISEE (moins de 600 Mo libres, hote a $hostMax % de CPU) : le PC swappe, le goulot est le PC, pas LiveKit" }
    elseif ($hostMax -ge 90) { $verdict = "HOTE SATURE (CPU du PC >= 90 %) : le goulot est le PC, pas LiveKit" }
    elseif ($panics -gt 0) { $verdict = "PANICS de lk sans saturation : bug du generateur probable (mets lk a jour)" }
    else { $verdict = "PERTE SANS SATURATION : suspecter Docker/reseau ou le serveur" }
    Write-Host ("  => {0}" -f $verdict) -ForegroundColor $(if ($verdict -like "OK*") { "Green" } else { "Red" })

    return [pscustomobject]@{
        Salles = $rooms; Attendu = $attendu; Connectes_60s = $c1; Connectes_fin = $c2; Perte_pct = $perte
        Hote_cpu_moy = (Moy $s.host); Hote_cpu_max = $hostMax; Ram_libre_min_mo = $ramMin
        lk_coeurs_moy = (Moy $s.lk); lk_coeurs_max = (Maxi $s.lk)
        Serveur_coeurs_moy = (Moy $s.live); Serveur_coeurs_max = (Maxi $s.live)
        vmmem_coeurs_moy = (Moy $s.vm)
        Panics = $panics; Erreurs = $erreurs; lk_sortis_tot = $ontQuitteTot; Verdict = $verdict
    }
}

# ---------------------------------------------------------------- Execution
$resultats = @()
try {
    foreach ($n in $Paliers) {
        $r = Invoke-Palier $n
        $resultats += $r
        Start-Sleep 20
        if ($r.Verdict -notlike "OK*" -and -not $ContinuerApresEchec) {
            Write-Host "Premier echec : arret (ajoute -ContinuerApresEchec pour tout enchainer)." -ForegroundColor Yellow
            break
        }
    }
} finally {
    Get-Process lk -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== RESUME ===" -ForegroundColor Cyan
$resultats | Select-Object Salles, Attendu, Connectes_fin, Perte_pct, Hote_cpu_max, Ram_libre_min_mo, lk_coeurs_moy, Serveur_coeurs_moy, vmmem_coeurs_moy, Panics, Verdict | Format-Table -AutoSize -Wrap
$resultats | Export-Csv (Join-Path $dir "resume.csv") -Delimiter ";" -NoTypeInformation -Encoding utf8

# Projection : cout du generateur a 10 salles, d'apres le premier palier.
$p = $resultats | Select-Object -First 1
if ($p -and $p.Salles -gt 0) {
    $parSalle = $p.lk_coeurs_moy / $p.Salles
    $proj = [Math]::Round($parSalle * 10, 1)
    Write-Host ("Generateur lk : {0:N2} coeur(s) par salle -> environ {1} coeur(s) pour 10 salles, sur {2} disponibles." -f $parSalle, $proj, $ncpu)
    if ($Cible -eq "Docker") {
        Write-Host ("Serveur (Docker) : {0:N2} coeur(s) par salle -> environ {1} pour 10 salles." -f ($p.Serveur_coeurs_moy / $p.Salles), [Math]::Round($p.Serveur_coeurs_moy / $p.Salles * 10, 1))
    }
}
Write-Host ("Fichiers : " + $dir) -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch {}
