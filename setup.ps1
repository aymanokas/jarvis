# One-time setup: Python environment, local models and the Ollama LLM.
# Run from the repo folder:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== Python environment"
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "== Voice model (Kokoro)"
New-Item -ItemType Directory -Force models | Out-Null
$base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
$ProgressPreference = "SilentlyContinue"
foreach ($f in "kokoro-v1.0.onnx", "voices-v1.0.bin") {
    if (-not (Test-Path "models\$f")) { Invoke-WebRequest "$base/$f" -OutFile "models\$f" }
}

Write-Host "== Wake word model"
.venv\Scripts\python.exe -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

Write-Host "== Language model (Ollama)"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
} else { $ollama = $ollama.Source }
& $ollama pull qwen3:8b

Write-Host "`nDone. Start JARVIS with run.bat"
