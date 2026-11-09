# Génère un faux enregistrement de réunion (français + anglais) à partir de reunion-01.script.txt,
# avec les voix Windows (System.Speech). Sert à MESURER la transcription avec un texte de référence connu.
#   .\tests\corpus\generer-reunion.ps1
# Sorties (dans tests\corpus) : reunion-01.wav (propre) et, si ffmpeg est installé, reunion-01-telephone.wav
# (16 kHz, bande passante réduite : plus proche d'un micro de portable).
Add-Type -AssemblyName System.Speech
$dossier = Split-Path -Parent $MyInvocation.MyCommand.Path
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voix = $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo }
"Voix installées :"
$voix | ForEach-Object { "  {0} ({1}, {2})" -f $_.Name, $_.Culture.Name, $_.Gender }

function Choisir($langue, $genre) {
    $c = $voix | Where-Object { $_.Culture.TwoLetterISOLanguageName -eq $langue -and $_.Gender -eq $genre } | Select-Object -First 1
    if (-not $c) { $c = $voix | Where-Object { $_.Culture.TwoLetterISOLanguageName -eq $langue } | Select-Object -First 1 }
    return $c
}
$genres = @{ "Marie" = "Female"; "Paul" = "Male"; "Sarah" = "Female" }
$sortie = Join-Path $dossier "reunion-01.wav"
$pb = New-Object System.Speech.Synthesis.PromptBuilder
foreach ($ligne in Get-Content (Join-Path $dossier "reunion-01.script.txt") -Encoding UTF8) {
    if (-not $ligne.Trim()) { continue }
    $p = $ligne.Split("|", 3)
    $v = Choisir $p[1] $genres[$p[0]]
    if (-not $v) { Write-Warning "Aucune voix '$($p[1])' installée : ligne ignorée ($($p[0]))."; continue }
    $pb.StartVoice($v)
    $pb.AppendText($p[2])
    $pb.EndVoice()
    $pb.AppendBreak([TimeSpan]::FromMilliseconds(600))
}
$synth.SetOutputToWaveFile($sortie)
$synth.Speak($pb)
$synth.Dispose()
"Écrit : $sortie"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    $tel = Join-Path $dossier "reunion-01-telephone.wav"
    ffmpeg -y -loglevel error -i $sortie -ar 16000 -ac 1 -af "highpass=f=150,lowpass=f=3400" $tel
    "Écrit : $tel"
}
