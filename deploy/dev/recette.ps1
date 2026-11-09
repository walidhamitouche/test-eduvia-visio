# Recette automatique (etape 21d) : rejoue, scenario par scenario, les tests qui le prouvent, et
# ecrit un compte rendu data/recette/recette-<date>.txt a joindre a docs/RECETTE.md.
# Ne remplace PAS les verifications a la main de docs/RECETTE.md (navigateurs, enregistrement reel).
#
#   .\deploy\dev\recette.ps1           # scenarios 1 a 8 (tests automatiques, ~4 min)
#   .\deploy\dev\recette.ps1 -E2E      # + tests de bout en bout (navigateur, LiveKit Docker reel)
param([switch]$E2E)
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force "data/recette" | Out-Null
$sortie = "data/recette/recette-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".txt"

$scenarios = [ordered]@{
    "1 Creneau strict (15h55 refuse, 16h OK, coupure 18h, 18h01 refuse)" = @("tests/unit/test_slot.py", "tests/integration/test_join.py", "tests/integration/test_lifecycle.py", "tests/integration/test_preview.py", "tests/integration/test_restart.py")
    "2 URL inventee ou modifiee" = @("tests/integration/test_links.py", "tests/integration/test_join.py", "tests/integration/test_preview.py", "tests/integration/test_web.py")
    "3 Micro coupe + exclusion definitive" = @("tests/integration/test_moderation.py", "tests/integration/test_presence_chat.py")
    "4 Salle d'attente" = @("tests/integration/test_lobby.py")
    "5 Nom nominatif : visio, presence, transcription" = @("tests/integration/test_join.py", "tests/integration/test_presence_chat.py", "tests/integration/test_tracks.py", "tests/integration/test_transcription.py", "tests/integration/test_transcripts.py")
    "6 Enregistrement S3 + webhooks signes" = @("tests/integration/test_recording.py", "tests/integration/test_egress.py", "tests/integration/test_webhooks_out.py", "tests/unit/test_webhook_signature.py", "tests/unit/test_webhook_docs.py")
    "7 Chat et presence exportables" = @("tests/integration/test_presence_chat.py", "tests/unit/test_presence.py")
    "8 Suppression par API + URL deja emise" = @("tests/integration/test_artifacts.py", "tests/integration/test_artifacts_s3.py", "tests/integration/test_transcripts.py", "tests/integration/test_storage.py")
}

$lignes = @("Recette automatique du " + (Get-Date -Format "yyyy-MM-dd HH:mm"), "")
$echecs = 0
foreach ($nom in $scenarios.Keys) {
    Write-Host "`n=== Scenario $nom ===" -ForegroundColor Cyan
    $res = uv run python -m pytest @($scenarios[$nom]) -q -p no:cacheprovider 2>&1
    $code = $LASTEXITCODE
    $resume = ($res | Select-Object -Last 1)
    $etat = if ($code -eq 0) { "OK " } else { "ECHEC"; $echecs++ }
    if ($code -ne 0) { $res | Select-Object -Last 25 | ForEach-Object { Write-Host $_ -ForegroundColor Red } }
    Write-Host "$etat  $resume"
    $lignes += "$etat  Scenario $nom : $resume"
}
$lignes += "9 10 reunions x 30 participants : NON AUTOMATISE ICI - a rejouer sur le serveur (deploy/dev/test-charge.ps1 depuis une autre machine ; voir DECISIONS.md section 6 quindecies)"

if ($E2E) {
    Write-Host "`n=== Tests de bout en bout (navigateur + LiveKit Docker) ===" -ForegroundColor Cyan
    uv run task e2e-docker
    $etat = if ($LASTEXITCODE -eq 0) { "OK " } else { "ECHEC"; $echecs++ }
    $lignes += "$etat  Tests de bout en bout (tests/e2e)"
}

$lignes += ""
$lignes += if ($echecs -eq 0) { "RESULTAT : scenarios 1 a 8 verts cote automatique. Reste les verifications a la main (docs/RECETTE.md) et le scenario 9." } else { "RESULTAT : $echecs echec(s)." }
$lignes | Out-File $sortie -Encoding utf8
Write-Host "`n=== RESUME ===" -ForegroundColor Cyan
$lignes | ForEach-Object { Write-Host $_ }
Write-Host "`nCompte rendu : $sortie" -ForegroundColor Green
