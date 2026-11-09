# Tout ce qui se teste sur un poste de dev pour l'etape 20 (etapes 20b a 20d), en une commande.
# Resume ecrit dans data/charge/complet-<date>.txt.
#
# Prerequis : docker-dev.ps1 up, l'API lancee dans un autre terminal (uv run task dev-lan), `lk` installe.
#
#   .\deploy\dev\test-complet.ps1                         # tests 1 a 4 (~15 min)
#   .\deploy\dev\test-complet.ps1 -Audio .\reunion.wav    # + transcription en parallele (test 5)
#   .\deploy\dev\test-complet.ps1 -Check                  # + uv run task check (4 min de plus)
param([string]$Audio = "", [switch]$Check, [string]$Base = "http://localhost:8000")
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force "data/charge" | Out-Null
$out = "data/charge/complet-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".txt"
Start-Transcript -Path $out | Out-Null
$resultats = [ordered]@{}
function Etape($nom, [scriptblock]$bloc) {
    Write-Host "`n=== $nom ===" -ForegroundColor Cyan
    $debut = Get-Date
    try { & $bloc; $ok = ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) } catch { Write-Host $_ -ForegroundColor Red; $ok = $false }
    $script:resultats[$nom] = if ($ok) { "OK" } else { "ECHEC" }
    Write-Host ("   duree {0:N0} s" -f ((Get-Date) - $debut).TotalSeconds)
}

# 0. Pre-requis
try { $h = Invoke-RestMethod "$Base/health" -TimeoutSec 5; Write-Host "API : $($h | ConvertTo-Json -Compress)" }
catch { Write-Host "L'API ne repond pas sur $Base : lance  uv run task dev-lan  dans un autre terminal." -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }
$apiKey = (dotenvx get API_KEY -f .env.development).Trim()

if ($Check) { Etape "1. uv run task check (qualite et 929+ tests)" { uv run task check } }

Etape "2. Charge de l'API : 10 reunions x 30 personnes, 2 tours (300 puis 300 reconnexions)" {
    uv run task load-api -- --base $Base --api-key $apiKey --meetings 10 --participants 30 --rounds 2
}

Etape "3. Charge LiveKit 2 x 30 pendant 5 min + 1 enregistrement (taille par heure)" {
    .\deploy\dev\test-charge.ps1 -Rooms 2 -Participants 30 -Minutes 5 -Recorded 1
}

Etape "4. Charge LiveKit 5 x 30 pendant 5 min + 2 enregistrements (limite de ce PC)" {
    .\deploy\dev\test-charge.ps1 -Rooms 5 -Participants 30 -Minutes 5 -Recorded 2
}

if ($Audio) {
    Etape "5. Transcription : 1, 2 puis 4 fichiers en parallele ($Audio)" {
        $dur = $null
        if (Get-Command ffprobe -ErrorAction SilentlyContinue) {
            $dur = [double](ffprobe -v error -show_entries format=duration -of csv=p=0 $Audio)
            Write-Host ("   duree du fichier : {0:N0} s" -f $dur)
        }
        foreach ($n in 1, 2, 4) {
            $t = Get-Date
            $jobs = 1..$n | ForEach-Object { Start-Process uv -PassThru -NoNewWindow -ArgumentList @("run", "task", "transcribe-file", "--", $Audio, "--vocab=") -RedirectStandardOutput "$env:TEMP\tr-$_.out" -RedirectStandardError "$env:TEMP\tr-$_.err" }
            $jobs | Wait-Process
            $s = ((Get-Date) - $t).TotalSeconds
            $ratio = if ($dur) { " (x{0:N2} la duree du fichier)" -f ($s / $dur) } else { "" }
            Write-Host ("   {0} en parallele : {1:N0} s{2}" -f $n, $s, $ratio)
        }
    }
} else { Write-Host "`n(test 5 saute : ajoute -Audio <fichier.wav> pour mesurer la transcription en parallele)" }

Write-Host "`n=== RESUME ===" -ForegroundColor Cyan
$resultats.GetEnumerator() | ForEach-Object { Write-Host ("{0,-6} {1}" -f $_.Value, $_.Key) -ForegroundColor $(if ($_.Value -eq "OK") { "Green" } else { "Red" }) }
Write-Host "`nResume complet : $out "
Stop-Transcript | Out-Null
