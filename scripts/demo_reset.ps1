# Atajo para Windows: deja el entorno listo para grabar la demo (ver scripts/demo_reset.py).
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
& "$PSScriptRoot\..\.venv\Scripts\python.exe" "$PSScriptRoot\demo_reset.py" @args
exit $LASTEXITCODE
