# Controle reseau de l'etape 18 : ports ouverts / fermes et certificat TLS du domaine TURN.
# A lancer depuis un AUTRE poste que le serveur (ce que voit un client), apres le deploiement.
#
#   .\deploy\dev\test-reseau.ps1 -Domaine turn.eduvia.app
#   .\deploy\dev\test-reseau.ps1 -Domaine turn.eduvia.app -Hote 203.0.113.10   # IP si le DNS n'est pas pret
#
# Verifie : 1. 443 et 7881 en TCP ouverts ; 2. Redis (6379), S3 (9000), API LiveKit (7880) FERMES ;
# 3. poignee de main TLS sur <Domaine>:443 avec un certificat valide, non expire, au bon nom.
# Le 443 UDP et le 7882 UDP ne se testent pas fiablement depuis PowerShell : voir docs/RESEAU.md
# (nmap -sU, ou la visio elle-meme avec ?relais=1). Code de sortie 0 = tout vert.

param(
    [Parameter(Mandatory = $true)][string]$Domaine,
    [string]$Hote = ""
)

$cible = if ($Hote) { $Hote } else { $Domaine }
$script:ok = 0
$script:ko = 0

function Check([string]$nom, [bool]$condition, [string]$detail = "") {
    if ($condition) { $script:ok++; Write-Host "  [OK]    $nom" -ForegroundColor Green }
    else { $script:ko++; Write-Host "  [ECHEC] $nom   $detail" -ForegroundColor Red }
}

function Port-Ouvert([string]$h, [int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $tache = $client.ConnectAsync($h, $port)
        return ($tache.Wait(4000) -and $client.Connected)
    } catch { return $false } finally { $client.Dispose() }
}

Write-Host "`n=== Ports TCP de $cible" -ForegroundColor Cyan
foreach ($p in 443, 7881) { Check "port $p ouvert" (Port-Ouvert $cible $p) }
foreach ($p in 6379, 9000, 7880) { Check "port $p FERME (interne)" (-not (Port-Ouvert $cible $p)) "ce port ne doit pas etre public" }

Write-Host "`n=== Certificat TLS de ${Domaine}:443" -ForegroundColor Cyan
$client = New-Object System.Net.Sockets.TcpClient
try {
    $client.Connect($cible, 443)
    $erreurs = [System.Net.Security.SslPolicyErrors]::None
    $rappel = { param($s, $c, $ch, $e) $script:erreursTls = $e; return $true }
    $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false, $rappel)
    $ssl.AuthenticateAsClient($Domaine)   # SNI = le domaine TURN
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
    Check "poignee de main TLS reussie" $true
    Check "certificat de confiance et au bon nom" ($script:erreursTls -eq [System.Net.Security.SslPolicyErrors]::None) "erreurs : $script:erreursTls"
    Check "certificat non expire (reste $([int]($cert.NotAfter - (Get-Date)).TotalDays) jours)" ($cert.NotAfter -gt (Get-Date).AddDays(7)) "expire le $($cert.NotAfter)"
} catch {
    Check "poignee de main TLS reussie" $false $_.Exception.Message
} finally { $client.Dispose() }

Write-Host "`n$($script:ok) OK, $($script:ko) en echec" -ForegroundColor $(if ($script:ko -eq 0) { "Green" } else { "Red" })
exit $(if ($script:ko -eq 0) { 0 } else { 1 })
