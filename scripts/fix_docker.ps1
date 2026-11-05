# Arregla el arranque de Docker Desktop cuando muere con
#   "remove ...\Docker\run\sailor-ingest.sock: The file cannot be accessed by the system"
# (o lo mismo con docker-secrets-engine\engine.sock). Pasa tras un apagado brusco: quedan sockets
# AF_UNIX huérfanos que Windows no deja borrar. No se borran: se renombran las carpetas que los
# contienen (Docker las vuelve a crear) y se arranca Docker sin interrumpirlo.
$ErrorActionPreference = "Stop"
Get-Process | Where-Object { $_.ProcessName -match '^(Docker Desktop|com\.docker\..*)$' } | Stop-Process -Force -Confirm:$false
Start-Sleep -Seconds 3
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
foreach ($dir in @("$env:LOCALAPPDATA\Docker\run", "$env:LOCALAPPDATA\docker-secrets-engine")) {
    if (Test-Path $dir) {
        Rename-Item $dir ((Split-Path $dir -Leaf) + ".stale-$stamp")
        Write-Host "renombrada: $dir"
    }
}
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "Docker Desktop arrancando; espera ~30 s y corre: docker version"
