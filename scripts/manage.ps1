param(
    [ValidateSet('Install','Start','Stop','Uninstall','Status','Preview','Pause','Resume','Exclude','Include','Backups','Rollback')]
    [string]$Action = 'Status',
    [string]$Project,
    [string]$Backup
)
$ErrorActionPreference = 'Stop'
$dataDir = Join-Path $env:USERPROFILE '.codex\project-follow'
$scriptPath = Join-Path $PSScriptRoot 'project_follow.py'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runName = 'GzdCodexProjectFollow'
[IO.Directory]::CreateDirectory($dataDir) | Out-Null

function Find-Python {
    $packages = @(Get-AppxPackage -Name 'OpenAI.CodexPrimaryRuntime*' | Sort-Object Version -Descending)
    foreach ($package in $packages) {
        $candidate = Join-Path $package.InstallLocation 'dependencies\python\python.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw 'Bundled Python runtime not found. Ask Codex to repair the launcher after the app update.'
}
function Worker-Running {
    $statusPath = Join-Path $dataDir 'status.json'
    if (-not (Test-Path -LiteralPath $statusPath)) { return $false }
    try {
        $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        $worker = Get-CimInstance Win32_Process -Filter "ProcessId = $($status.pid)"
        return ($null -ne $worker -and $worker.CommandLine -like ('*' + $scriptPath + '* watch*'))
    } catch { return $false }
}
function Stop-Worker {
    [IO.File]::WriteAllText((Join-Path $dataDir 'stop'), 'stop')
    for ($i=0; $i -lt 30; $i++) {
        if (-not (Worker-Running)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw 'Worker is still completing a transaction. Retry shortly; it was not force-terminated.'
}
function Start-Worker {
    if (Worker-Running) { return }
    $stopPath = Join-Path $dataDir 'stop'
    if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath }
    $pythonWindowless = Join-Path (Split-Path (Find-Python)) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python runtime not found.' }
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = ('"' + $pythonWindowless + '" "' + $scriptPath + '" watch');
        ProcessStartupInformation = $startup
    }
    if ($created.ReturnValue -ne 0) { throw ('Independent launch failed: ' + $created.ReturnValue) }
    for ($i=0; $i -lt 24; $i++) {
        Start-Sleep -Milliseconds 500
        if (Worker-Running) {
            $status = Get-Content -LiteralPath (Join-Path $dataDir 'status.json') -Raw | ConvertFrom-Json
            if ($status.pid -eq $created.ProcessId) { return }
        }
    }
    throw 'Worker did not report a fresh heartbeat. Check status before relying on synchronization.'
}

switch ($Action) {
    'Install' {
        # Resolve the runtime on each Windows login so runtime package updates do not pin a stale executable.
        $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $command = '"' + $windowsPowerShell + '" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Action Start'
        if (-not (Test-Path -LiteralPath $runKey)) { New-Item -Path $runKey | Out-Null }
        New-ItemProperty -LiteralPath $runKey -Name $runName -PropertyType String -Value $command -Force | Out-Null
        Start-Worker
    }
    'Start' { Start-Worker }
    'Stop' { Stop-Worker }
    'Uninstall' {
        Stop-Worker
        Remove-ItemProperty -LiteralPath $runKey -Name $runName -ErrorAction SilentlyContinue
        Write-Output 'Background startup removed. Skill, catalog, backups and project data are retained.'
        return
    }
    { $_ -in @('Pause','Resume','Exclude','Include','Rollback') } {
        Stop-Worker
        $arguments = @($scriptPath, $Action.ToLowerInvariant())
        if ($Project) { $arguments += @('--project', $Project) }
        if ($Backup) { $arguments += @('--backup', $Backup) }
        & (Find-Python) @arguments
        if ($LASTEXITCODE -ne 0) { throw 'Management operation failed. Worker remains stopped for review.' }
        Start-Worker
    }
    default {
        & (Find-Python) $scriptPath $Action.ToLowerInvariant()
        if ($LASTEXITCODE -ne 0) { throw 'Read-only query failed.' }
        if ($Action -eq 'Status') { Write-Output ('WorkerRunning: ' + (Worker-Running)) }
        return
    }
}
& (Find-Python) $scriptPath status
Write-Output ('WorkerRunning: ' + (Worker-Running))
