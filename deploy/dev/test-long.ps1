# Test long (etape 14d) : reunion de N minutes avec 2 navigateurs a camera/micro simules,
# mesure CPU/RAM d'Egress toutes les 30 s, puis taille par heure et verification du MP4 fusionne.
# Prerequis : docker-dev.ps1 up, recepteur de webhooks, API lancee (uv run task dev-lan) avec
# RECORDING_SEGMENT_SECONDS=600 et ffmpeg installe, Google Chrome installe.
#
#   .\deploy\dev\test-long.ps1 -Minutes 5      # essai court
#   .\deploy\dev\test-long.ps1 -Minutes 60     # le vrai test
#   .\deploy\dev\test-long.ps1 -Minutes 60 -Reprise   # + redemarrage d'Egress puis de l'API en route
param([int]$Minutes = 60, [switch]$Reprise)
$ErrorActionPreference = "Stop"
$base = "http://localhost:8000"
$key = dotenvx get API_KEY -f .env.development
$h = @{ Authorization = "Bearer $key" }
$debut = Get-Date
$fmt = "yyyy-MM-ddTHH:mm:sszzz"
$corps = @{
    title = "Test long $Minutes min"; starts_at = $debut.ToString($fmt)
    ends_at = $debut.AddMinutes($Minutes + 10).ToString($fmt); waiting_room = $false; recording = $true
    organizers = @(@{ display_name = "Marie Durand"; external_id = "staff_42" })
    metadata = @{ eduvia_session_id = 4242 }
} | ConvertTo-Json -Depth 6
$m = Invoke-RestMethod -Method Post "$base/v1/meetings" -Headers $h -ContentType "application/json" -Body $corps
Write-Host "Reunion $($m.id) creee" -ForegroundColor Green

$chrome = (Get-Command chrome -ErrorAction SilentlyContinue).Source
if (-not $chrome) { $chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe" }
$flags = @("--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--no-first-run")
$p1 = Start-Process $chrome -PassThru -ArgumentList ($flags + @("--user-data-dir=$env:TEMP\visio-fake1", $m.organizer_urls[0]))
Start-Sleep 5
$p2 = Start-Process $chrome -PassThru -ArgumentList ($flags + @("--user-data-dir=$env:TEMP\visio-fake2", $m.participant_url))

$egress = (docker ps --format "{{.Names}}" | Select-String "egress").ToString()
$csv = "data\mesures-$($m.id).csv"
"minute;cpu_pct;mem" | Set-Content $csv
$total = $Minutes * 2
$faitEgress = $false; $faitApi = $false
for ($i = 1; $i -le $total; $i++) {
    Start-Sleep 30
    $s = docker stats --no-stream --format "{{.CPUPerc}};{{.MemUsage}}" $egress
    "{0};{1}" -f ($i / 2), $s | Add-Content $csv
    if ($i % 10 -eq 0) { Write-Host ("  minute {0} / {1} : Egress {2}" -f ($i / 2), $Minutes, $s) }
    if ($Reprise -and -not $faitEgress -and $i -ge [int]($total / 3)) {
        Write-Host ">>> Redemarrage d'Egress (les segments deja envoyes doivent etre conserves)" -ForegroundColor Yellow
        docker restart $egress | Out-Null; $faitEgress = $true
    }
    if ($Reprise -and -not $faitApi -and $i -ge [int]($total * 2 / 3)) {
        Write-Host ">>> Fais maintenant Ctrl+C dans la fenetre de l'API, attends 10 s, relance-la (uv run task dev-lan), puis Entree ici." -ForegroundColor Yellow
        Read-Host; $faitApi = $true
    }
}
Invoke-RestMethod -Method Post "$base/v1/meetings/$($m.id)/end" -Headers $h | Out-Null
Stop-Process -Id $p1.Id, $p2.Id -ErrorAction SilentlyContinue
Write-Host "Reunion terminee. Fusion des segments puis verification (attente max 15 min)..." -ForegroundColor Yellow
uv run task recording-check $m.id --wait 900 --probe
$code = $LASTEXITCODE

$rows = Import-Csv $csv -Delimiter ";"
$cpu = $rows | ForEach-Object { [double]($_.cpu_pct -replace "%", "") }
Write-Host ("CPU Egress : moyenne {0:N0} %  max {1:N0} %   (100 % = 1 coeur)" -f ($cpu | Measure-Object -Average).Average, ($cpu | Measure-Object -Maximum).Maximum)
Write-Host "RAM Egress : voir la colonne mem dans $csv"
$mp4 = Get-ChildItem "data\recordings\$($m.id)" -Filter *.mp4 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($mp4) {
    $mo = $mp4.Length / 1MB
    Write-Host ("MP4 : {0:N0} Mo pour {1} min  =>  {2:N0} Mo par heure, {3:N1} Go pour 8 h" -f $mo, $Minutes, ($mo * 60 / $Minutes), ($mo * 480 / $Minutes / 1024))
    Invoke-Item $mp4.FullName
}
exit $code
