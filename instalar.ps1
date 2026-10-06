param([switch]$SoAbrir)
# Ecolyra — instalador. Chamado pelo INSTALAR.bat (instala) e pelo "Abrir Ecolyra.bat" (-SoAbrir).
$ErrorActionPreference = "Continue"
$pasta = $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Titulo($t) { Write-Host "  $t" -ForegroundColor Green }
function Passo($n, $t) { Write-Host "[$n/4] $t" -ForegroundColor Cyan }
function Falha($t) { Write-Host "`n  $t`n" -ForegroundColor Red; exit 1 }

function Achar-Python {
    # o "python" da Microsoft Store pode ser só um atalho que abre a loja: testa de verdade
    foreach ($c in @("py", "python", "python3")) {
        if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { continue }
        try {
            $exe = & $c -c "import sys;assert sys.version_info>=(3,10);print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe.Trim() }
        } catch {}
    }
    foreach ($v in "313", "312", "311", "310") {
        $p = "$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe"
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Abrir($py) {
    $pyw = $py -replace "python(3(\.\d+)?)?\.exe$", "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $py }
    Start-Process -FilePath $pyw -ArgumentList "`"$pasta\ecolyra.pyw`"" -WorkingDirectory $pasta
    return $pyw
}

# ---------- só abrir (Abrir Ecolyra.bat) ----------
if ($SoAbrir) {
    $py = Achar-Python
    if ($py) {
        & $py -c "import winrt.windows.media.control, pyaudiowpatch" 2>$null
        if ($LASTEXITCODE -eq 0) { Abrir $py | Out-Null; exit 0 }
    }
    Write-Host "O Ecolyra ainda não está instalado. Instalando agora..." -ForegroundColor Yellow
}

Write-Host ""
Titulo "Ecolyra · instalação"
Titulo "letras traduzidas em tempo real que escutam a música"
Write-Host ""

# ---------- 1. Python ----------
$py = Achar-Python
if (-not $py) {
    Passo 1 "Python não encontrado — instalando o Python 3.12 pelo winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Falha "Não achei o winget. Instale o Python em https://www.python.org/downloads/ (marque 'Add python.exe to PATH') e rode o INSTALAR.bat de novo."
    }
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements | Out-Host
    $py = Achar-Python
    if (-not $py) { Falha "Não consegui instalar o Python sozinho. Baixe em https://www.python.org/downloads/ e rode o INSTALAR.bat de novo." }
}
$ver = & $py -c "import sys;print(sys.version.split()[0])"
Passo 1 "Python $ver  ($py)"

# ---------- 2. Bibliotecas ----------
Passo 2 "Instalando as bibliotecas (na primeira vez leva alguns minutos)..."
$pipArgs = @("-m", "pip", "install", "--user", "--disable-pip-version-check", "--quiet")
& $py @pipArgs --upgrade pip 2>&1 | Out-Null
& $py @pipArgs -r "$pasta\requirements.txt"
if ($LASTEXITCODE -ne 0) { Falha "Falhou ao instalar as bibliotecas (veja a mensagem acima)." }
# Shazam: núcleo 1.2.0 já compilado + shazamio sem puxar o 1.1.2 (que exige Rust)
& $py @pipArgs "shazamio-core>=1.2.0"
& $py @pipArgs --no-deps shazamio
& $py -c "import winrt.windows.media.control, PIL, requests, pyaudiowpatch, pystray, pycaw, shazamio" 2>$null
if ($LASTEXITCODE -ne 0) { Falha "Alguma biblioteca não instalou direito (veja a mensagem acima)." }
Write-Host "       tudo certo" -ForegroundColor DarkGray

# ---------- 3. Atalho ----------
Passo 3 "Criando o atalho 'Ecolyra' na área de trabalho..."
$pyw = $py -replace "python(3(\.\d+)?)?\.exe$", "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
$desk = [Environment]::GetFolderPath("Desktop")
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\Ecolyra.lnk")
$s.TargetPath = $pyw
$s.Arguments = "`"$pasta\ecolyra.pyw`""
$s.WorkingDirectory = $pasta
$s.IconLocation = "$pasta\icone.ico"
$s.Description = "Ecolyra — letras traduzidas em tempo real"
$s.Save()

# ---------- 4. Abrir ----------
Passo 4 "Abrindo o Ecolyra..."
Abrir $py | Out-Null
Write-Host ""
Titulo "Pronto! Toque uma música no Spotify, YouTube ou outro player."
Write-Host "  Da próxima vez é só usar o atalho 'Ecolyra' na área de trabalho."
Write-Host ""
Start-Sleep -Seconds 6
exit 0
