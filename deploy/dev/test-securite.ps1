# T1.11 - Un jeton de PARTICIPANT ne doit pas pouvoir terminer / couper / exclure.
# Usage : .\deploy\dev\test-securite.ps1 -Jeton "eyJ..."   (la partie apres /j/ du lien participant)
param([Parameter(Mandatory = $true)][string]$Jeton)

function Test-Room($route, $corps) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Method Post "http://localhost:8000/v1/room/$route" `
            -ContentType "application/json" -Body ($corps | ConvertTo-Json)
        "$route -> $($r.StatusCode) $($r.Content)   (INATTENDU : devrait etre refuse)"
    } catch {
        $resp = $_.Exception.Response
        if ($null -eq $resp) { "$route -> erreur reseau : $($_.Exception.Message)"; return }
        $sr = New-Object IO.StreamReader($resp.GetResponseStream())
        "$route -> $([int]$resp.StatusCode) $($sr.ReadToEnd())"
    }
}

Write-Host "--- Jeton valide de participant (attendu : 403 organizer_required x3)"
Test-Room "end"    @{ token = $Jeton }
Test-Room "mute"   @{ token = $Jeton; identity = "quelquun"; source = "microphone" }
Test-Room "remove" @{ token = $Jeton; identity = "quelquun" }

Write-Host "--- Jeton modifie d'un caractere (attendu : 404 invalid_link)"
$dernier = $Jeton.Substring($Jeton.Length - 1)
$autre = if ($dernier -eq 'A') { 'B' } else { 'A' }
Test-Room "end" @{ token = ($Jeton.Substring(0, $Jeton.Length - 1) + $autre) }
