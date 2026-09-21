# Future operator entrypoint. Provisions pinned Python dependencies, then only
# observes the already reviewed/deployed lifecycle. No deployment or acquisition.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ReviewedCommit,
    [Parameter(Mandatory=$true)][string]$LifecyclePath,
    [string]$BasePython = 'python'
)
$ErrorActionPreference = 'Stop'
$secret = $null
$previousVolume = $env:STAGE2_BROKER_VOLUME
Push-Location $PSScriptRoot
try {
    Remove-Item Env:HA_TOKEN -ErrorAction SilentlyContinue
    if ((git branch --show-current) -ne 'physical-v3-hardening' -or
        (git rev-parse HEAD) -ne $ReviewedCommit) { throw 'Reviewed source mismatch' }
    if (git status --porcelain --untracked-files=no) { throw 'Commit reviewed source first' }
    $lifecycle = Get-Content -LiteralPath $LifecyclePath -Raw | ConvertFrom-Json
    if (-not $lifecycle.prepared -or $lifecycle.repository.commit -ne $ReviewedCommit) {
        throw 'New reviewed lifecycle/deployment required; preserve the old artifact'
    }
    $runtimeDir = Join-Path $PSScriptRoot '.venv-stage2-runtime'
    $experimentPython = Join-Path $runtimeDir 'Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $experimentPython)) {
        & $BasePython -E -s -m venv $runtimeDir
        if ($LASTEXITCODE) { throw 'Separate experiment environment creation failed' }
    }
    & $experimentPython -E -s -m pip --disable-pip-version-check install --no-deps --requirement (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE) { throw 'Pinned requirements installation failed; no live work attempted' }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N')
    $dependencies = "analysis/stage2-dependencies-$stamp.json"
    & $experimentPython -E -s stage2_dependencies.py --output $dependencies
    if ($LASTEXITCODE) { throw 'Dependency verification failed; no live work attempted' }
    $env:STAGE2_BROKER_VOLUME = $lifecycle.volume_name
    $preflight = "analysis/stage2-preflight-$stamp.json"
    $secret = Read-Host 'HA_TOKEN (hidden)' -AsSecureString
    $env:HA_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    & $experimentPython -E -s stage2_runner.py preflight --plan-sha256 1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0 --expected-commit $ReviewedCommit --volume $env:STAGE2_BROKER_VOLUME --lifecycle $LifecyclePath --output $preflight
    if ($LASTEXITCODE) { throw "Read-only preflight failed; preserve $preflight and stop" }
    Write-Output "STOP: review $preflight and $dependencies. No acquisition was requested."
} finally {
    Remove-Item Env:HA_TOKEN -ErrorAction SilentlyContinue
    if ($null -ne $secret) { $secret.Dispose() }
    $env:STAGE2_BROKER_VOLUME = $previousVolume
    Pop-Location
}
