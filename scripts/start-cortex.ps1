$ErrorActionPreference = 'Stop'
$cortexUrl = 'http://127.0.0.1:8001/admin'
function Test-Cortex {
    try {
        $response = Invoke-WebRequest -Uri $cortexUrl -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200 -and $response.Content.Contains('Cortex')
    } catch { return $false }
}
if (-not (Test-Cortex)) {
    if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
        throw 'A porta 8001 esta ocupada por outro servico. Nenhum processo foi encerrado.'
    }
    $cortexPython = (Get-Command python -ErrorAction Stop).Source
    $cortexLogs = Join-Path $PSScriptRoot 'output'
    New-Item -ItemType Directory -Path $cortexLogs -Force | Out-Null
    Start-Process -FilePath $cortexPython -ArgumentList '-m','uvicorn','simulator_app:app','--host','127.0.0.1','--port','8001' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $cortexLogs 'overview-server.log') -RedirectStandardError (Join-Path $cortexLogs 'overview-server-error.log')
    for ($cortexAttempt = 0; $cortexAttempt -lt 20; $cortexAttempt++) {
        if (Test-Cortex) { break }
        Start-Sleep -Seconds 1
    }
    if (-not (Test-Cortex)) { throw "Nao foi possivel iniciar o Cortex. Confira $cortexLogs\overview-server-error.log" }
}
Start-Process $cortexUrl
