# Controle de securite des images de production (etape 19) : aucun secret en clair.
#   .\deploy\dev\check-images.ps1            (images deja construites : docker build, voir docs/INSTALL.md)
# Equivalent PowerShell de deploy/check-images.sh. Code de sortie 1 si un secret est trouve.
param([string[]]$Images = @("eduvia-visio-api", "eduvia-visio-transcriber"))

$fail = $false
# Dans le conteneur : pas de .env.keys, et aucune valeur de .env.production sans "encrypted:".
$check = 'test ! -e /app/.env.keys && echo KEYS_ABSENT; echo CLEAR=$(grep -E ''^[A-Z_]+='' /app/.env.production | grep -v ''^DOTENV_PUBLIC_KEY'' | grep -v -c ''encrypted:'')'

foreach ($img in $Images) {
    Write-Host "== $img" -ForegroundColor Cyan

    $history = docker history --no-trunc $img | Select-String -Pattern 'DOTENV_PRIVATE_KEY|API_KEY=|SECRET=|PASSWORD'
    if ($history) { Write-Host "  ECHEC historique : un secret apparait" -ForegroundColor Red; $fail = $true }
    else { Write-Host "  OK   historique : rien" -ForegroundColor Green }

    $envVars = docker inspect $img --format '{{json .Config.Env}}' | Select-String -Pattern 'DOTENV_PRIVATE_KEY|SECRET|PASSWORD'
    if ($envVars) { Write-Host "  ECHEC variables d'environnement de l'image" -ForegroundColor Red; $fail = $true }
    else { Write-Host "  OK   variables d'environnement : rien" -ForegroundColor Green }

    $files = (docker run --rm --entrypoint sh $img -c $check) -join " "
    if ($files -match 'KEYS_ABSENT' -and $files -match 'CLEAR=0') {
        Write-Host "  OK   fichiers : pas de .env.keys, .env.production entierement chiffre" -ForegroundColor Green
    } else {
        Write-Host "  ECHEC fichiers ($files)" -ForegroundColor Red; $fail = $true
    }
}

if ($fail) { Write-Host "`nSecret trouve dans une image." -ForegroundColor Red; exit 1 }
Write-Host "`nAucun secret en clair dans les images." -ForegroundColor Green
