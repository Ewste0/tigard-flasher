param([switch]$SkipDriver, [switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location $root
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Run-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE) { throw "$Program failed (exit $LASTEXITCODE)." }
}
function Find-Python {
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Programs/Python/Python313/python.exe'), 'py', 'python')
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
        $prefix = @(); if ($candidate -eq 'py') { $prefix = @('-3') }
        try {
            $path = & $candidate @prefix -c 'import sys; assert sys.version_info >= (3,10); print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path $path)) { return [string]$path }
        } catch { continue }
    }
    return $null
}
try {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    if ($arch -ne 'AMD64' -or [Environment]::OSVersion.Version.Major -lt 10) { throw 'Automatic setup currently supports Windows 10/11 x64. See docs/WINDOWS_USB.md for manual setup.' }
    Write-Host 'Tigard Flasher setup: Python, application and USB support.'
    $python = Find-Python
    if (-not $python) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw 'Python 3.10+ and winget were not found. Install Python from https://www.python.org/downloads/windows/ and run Setup Windows.cmd again.'
        }
        Run-Checked 'winget' @('install','--exact','--id','Python.Python.3.13','--scope','user','--accept-package-agreements','--accept-source-agreements')
        $python = Find-Python
        if (-not $python) { throw 'Python was installed; close this window and run Setup Windows.cmd again.' }
    }
    $venv = Join-Path $root '.venv/Scripts/python.exe'
    if (-not (Test-Path $venv)) { Run-Checked $python @('-m','venv',(Join-Path $root '.venv')) }
    Run-Checked $venv @('-m','pip','install','--upgrade','pip')
    Run-Checked $venv @('-m','pip','install','--upgrade','-e',$root)
    Run-Checked $venv @('-m','spc5tool','usb-check','--backend-only')

    if (-not $SkipDriver) {
        Write-Host 'Connect exactly one Tigard. Close serial/JTAG applications before driver setup.'
        Write-Host 'The libusb-win32 device driver replaces the Tigard COM-port driver. Windows will request administrator permission.'
        $cache = Join-Path $root '.setup/windows-usb'
        New-Item -ItemType Directory -Force $cache | Out-Null
        $release = 'https://github.com/Ewste0/tigard-flasher/releases/download/v0.7.2'
        $manifest = Invoke-RestMethod "$release/driver-manifest.json"
        $name = 'tigard-usb-driver-v0.7.2.zip'
        if ($manifest.name -ne $name -or $manifest.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid driver release manifest' }
        $zip = Join-Path $cache $name
        if (-not (Test-Path $zip) -or (Get-FileHash $zip -Algorithm SHA256).Hash -ne $manifest.sha256) {
            Invoke-WebRequest "$release/$name" -OutFile $zip
        }
        if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $manifest.sha256) { throw 'Driver archive hash mismatch; nothing executed' }
        Expand-Archive $zip -DestinationPath "$cache/helper" -Force
        $helper = Join-Path $cache 'helper/tigard-usb-driver.exe'
        if (-not (Test-Path $helper)) { throw 'Driver helper missing from verified archive' }
        & $helper --check
        $check = $LASTEXITCODE
        if ($check -eq 10) {
            $destination = Join-Path $cache 'extracted-driver'
            $process = Start-Process -FilePath $helper -ArgumentList @('--install', ('"' + $destination + '"')) -Verb RunAs -Wait -PassThru
            if ($process.ExitCode -ne 0) { throw "USB driver installation failed or was cancelled (exit $($process.ExitCode))." }
        } elseif ($check -ne 0) {
            throw 'USB driver setup could not select one Tigard. Connect one adapter, disconnect other FT2232H devices, then rerun setup.'
        }
        Run-Checked $venv @('-m','spc5tool','usb-check')
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Tigard Flasher.lnk'))
    $shortcut.TargetPath = $venv
    $shortcut.Arguments = '"' + (Join-Path $root 'launch.py') + '"'
    $shortcut.WorkingDirectory = $root
    $shortcut.Save()
    Write-Host 'Setup complete. No JTAG or MCU memory operation was performed.'
    if (-not $NoLaunch) { Run-Checked $venv @((Join-Path $root 'launch.py')) }
} catch {
    Write-Host ("Setup failed: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
