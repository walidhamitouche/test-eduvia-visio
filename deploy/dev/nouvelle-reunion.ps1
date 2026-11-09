# Cree une reunion de test deja commencee et affiche les liens.
# A lancer depuis la racine du depot, API lancee (uv run task dev).
#
#   .\deploy\dev\nouvelle-reunion.ps1                                  # reunion simple (2 h)
#   .\deploy\dev\nouvelle-reunion.ps1 -DeuxOrganisateurs               # Marie + Paul
#   .\deploy\dev\nouvelle-reunion.ps1 -SalleAttente                    # avec salle d'attente
#   .\deploy\dev\nouvelle-reunion.ps1 -DureeMinutes 7                  # creneau court
#   .\deploy\dev\nouvelle-reunion.ps1 -Nominatifs "Lina Benali","Karim Zidane"   # liens nominatifs
#   .\deploy\dev\nouvelle-reunion.ps1 -DansMinutes 30                  # commence dans 30 min
#   .\deploy\dev\nouvelle-reunion.ps1 -Enregistrement                  # enregistree (etape 14)
#   .\deploy\dev\nouvelle-reunion.ps1 -Transcription                   # une piste audio par personne (etape 16b)

param(
    [int]$DansMinutes = 0,
    [int]$DureeMinutes = 120,
    [switch]$DeuxOrganisateurs,
    [switch]$SalleAttente,
    [switch]$Enregistrement,
    [switch]$Transcription,
    [string[]]$Nominatifs = @()
)

$ErrorActionPreference = "Stop"
$base = "http://localhost:8000"
$key = dotenvx get API_KEY -f .env.development
$h = @{ Authorization = "Bearer $key" }
$debut = (Get-Date).AddMinutes($DansMinutes)
$fin = $debut.AddMinutes($DureeMinutes)
$fmt = "yyyy-MM-ddTHH:mm:sszzz"

$orgas = @(@{ display_name = "Marie Durand"; external_id = "staff_42" })
if ($DeuxOrganisateurs) {
    $orgas += @{ display_name = "Paul Martin"; external_id = "staff_77" }
}

$corps = @{
    title        = "Essai Eduvia Visio"
    starts_at    = $debut.ToString($fmt)
    ends_at      = $fin.ToString($fmt)
    waiting_room = [bool]$SalleAttente
    recording    = [bool]$Enregistrement
    transcription = [bool]$Transcription
    organizers   = $orgas
    metadata     = @{ eduvia_session_id = 4242 }
}
$json = $corps | ConvertTo-Json -Depth 6
$m = Invoke-RestMethod -Method Post "$base/v1/meetings" -Headers $h `
    -ContentType "application/json" -Body $json

Write-Host ""
Write-Host "Reunion : $($m.id)  (statut : $($m.status))" -ForegroundColor Green
Write-Host "Salle d'attente : $([bool]$SalleAttente)   Enregistrement : $([bool]$Enregistrement)   Fin prevue : $($fin.ToString('HH:mm:ss'))"

Write-Host "`n[O] Lien organisateur 1 (Marie) :`n$($m.organizer_urls[0])"
if ($DeuxOrganisateurs) {
    Write-Host "`n[O2] Lien organisateur 2 (Paul, co-animateur) :`n$($m.organizer_urls[1])"
}
Write-Host "`n[P] Lien participant GENERIQUE (le nom se saisit) :`n$($m.participant_url)"

$i = 1
foreach ($nom in $Nominatifs) {
    $lien = @{ role = "participant"; display_name = $nom; external_id = "stu_$i" } | ConvertTo-Json
    $r = Invoke-RestMethod -Method Post "$base/v1/meetings/$($m.id)/links" -Headers $h `
        -ContentType "application/json" -Body $lien
    Write-Host "`n[P nominatif] $nom (external_id stu_$i) :`n$($r.url)"
    $i++
}

Write-Host "`nId a recopier dans les commandes suivantes : $($m.id)" -ForegroundColor Yellow
