# Test de l'enregistrement (etapes 14b et 14c) : tout est automatique sauf la parole dans le navigateur.
# A lancer depuis la racine du depot, avec Docker (docker-dev.ps1 up), l'API (uv run task dev) et
# le recepteur de webhooks (voir plus bas) deja lances.
#
#   .\deploy\dev\test-enregistrement.ps1                          # cas nominal : attendu "ready"
#   .\deploy\dev\test-enregistrement.ps1 -Mode SansEnregistrement # attendu : aucun recording.*
#   .\deploy\dev\test-enregistrement.ps1 -Mode SansPiste          # attendu : "failed" (aucune piste)
#   .\deploy\dev\test-enregistrement.ps1 -Mode Recette            # etape 14c : deux personnes + partage
#                                                                  # d'ecran, puis le MP4 s'ouvre pour toi
#
# Ce que fait le script : cree la reunion, ouvre le lien organisateur dans ton navigateur,
# attend que tu aies parle, TERMINE la reunion par l'API (pas de bouton), puis lance
# `uv run task recording-check` qui verifie base, S3 et webhooks et rend OK / ECHEC.
#
# Recepteur de webhooks (autre fenetre), sur le port 9100 (le 9000 est deja pris par le S3) :
#   $env:WEBHOOK_SECRET = dotenvx get WEBHOOK_SECRET -f .env.development
#   uv run python deploy/dev/webhook-receiver.py 9100
# et l'API doit etre lancee avec :
#   $env:WEBHOOK_URL = "http://localhost:9100/hooks/visio"
#   uv run task dev

param(
    [ValidateSet("Nominal", "SansEnregistrement", "SansPiste", "Recette")][string]$Mode = "Nominal",
    [int]$DureeMinutes = 30
)

$ErrorActionPreference = "Stop"
$base = "http://localhost:8000"
$key = dotenvx get API_KEY -f .env.development
$h = @{ Authorization = "Bearer $key" }
$enregistre = ($Mode -ne "SansEnregistrement")

$debut = Get-Date
$fin = $debut.AddMinutes($DureeMinutes)
$fmt = "yyyy-MM-ddTHH:mm:sszzz"
$corps = @{
    title        = "Test enregistrement ($Mode)"
    starts_at    = $debut.ToString($fmt)
    ends_at      = $fin.ToString($fmt)
    waiting_room = $false
    recording    = $enregistre
    organizers   = @(@{ display_name = "Marie Durand"; external_id = "staff_42" })
    metadata     = @{ eduvia_session_id = 4242 }
} | ConvertTo-Json -Depth 6
$m = Invoke-RestMethod -Method Post "$base/v1/meetings" -Headers $h -ContentType "application/json" -Body $corps
Write-Host "Reunion $($m.id) creee (recording = $enregistre)" -ForegroundColor Green

Start-Process $m.organizer_urls[0]
switch ($Mode) {
    "Nominal"            { Write-Host "Dans le navigateur : autorise micro et camera, parle ~30 s, partage un ecran ~10 s." }
    "SansEnregistrement" { Write-Host "Dans le navigateur : entre, parle ~10 s. Aucun indicateur d'enregistrement ne doit apparaitre." }
    "Recette"            {
        Write-Host "Recette 14c : deux personnes, donc deux fenetres. Le lien PARTICIPANT est dans le presse-papiers :"
        Write-Host "ouvre-le dans un AUTRE navigateur (ou une fenetre privee) et saisis le nom Lina."
        Write-Host "  0. Mets un casque (sinon les deux fenetres se renvoient le son et l'echo fausse l'ecoute)."
        Write-Host "  1. Verifie que l'indicateur 'Enregistrement en cours' s'allume dans les DEUX fenetres."
        Write-Host "  2. Marie (fenetre organisateur) : dis 'Je suis Marie, premiere phrase', puis coupe ton micro."
        Write-Host "  3. Lina : active ton micro et dis 'Je suis Lina, deuxieme phrase'."
        Write-Host "  4. Marie : partage un ecran ou une fenetre avec du texte bien lisible (pas un ecran noir) ~15 s."
        Write-Host "  5. Reste ~1 minute au total, puis reviens ici."
        Set-Clipboard -Value $m.participant_url
        Write-Host "Lien participant : $($m.participant_url)"
    }
    "SansPiste"          {
        Write-Host "Dans le navigateur : il ne faut publier AUCUNE piste. Si le navigateur a memorise ton autorisation,"
        Write-Host "clique sur le cadenas a gauche de l'adresse > Micro et Camera > Bloquer, recharge la page, entre,"
        Write-Host "et reste ~30 s dans la salle sans micro ni camera."
    }
}
Read-Host "Appuie sur Entree quand c'est fait (la reunion sera terminee par l'API)"

Invoke-RestMethod -Method Post "$base/v1/meetings/$($m.id)/end" -Headers $h | Out-Null
Write-Host "Reunion terminee. Verification (attente max 90 s de l'issue)..." -ForegroundColor Yellow

switch ($Mode) {
    "Nominal"   { uv run task recording-check $m.id --wait 90 --probe }
    "Recette"   { uv run task recording-check $m.id --wait 90 --probe }
    "SansPiste" { uv run task recording-check $m.id --wait 120 --expect failed }
    default     { uv run task recording-check $m.id }
}
$code = $LASTEXITCODE
if ($Mode -eq "Recette") {
    $mp4 = Get-ChildItem "data\recordings\$($m.id)" -Filter *.mp4 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($mp4) {
        Write-Host "Ouverture du MP4 : $($mp4.FullName)" -ForegroundColor Yellow
        Write-Host "A juger a l'oreille et a l'oeil : les deux phrases audibles, les deux videos, le partage d'ecran lisible."
        Invoke-Item $mp4.FullName
    }
}
exit $code
