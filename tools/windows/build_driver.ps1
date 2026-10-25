# Build the narrow Tigard installer from pinned upstream source/driver inputs.
param([string]$Output = "driver-dist")
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if ($env:RUNNER_TEMP) { $work = Join-Path $env:RUNNER_TEMP 'tigard-driver-build' }
else { $work = Join-Path $env:TEMP ('tigard-driver-build-' + [guid]::NewGuid()) }
New-Item -ItemType Directory -Force $work, $Output | Out-Null
function Fetch($url, $name, $sha) {
    $path = Join-Path $work $name
    Invoke-WebRequest -Uri $url -OutFile $path
    if ((Get-FileHash $path -Algorithm SHA256).Hash -ne $sha) { throw "Hash mismatch: $name" }
    return $path
}
$source = Fetch 'https://github.com/pbatard/libwdi/archive/refs/tags/v1.5.1.tar.gz' 'libwdi.tar.gz' 'a695e93db0977dfdc5c6a99a4ea91b22f9027547d0177b2a0f3075078643c929'
$usb0 = Fetch 'https://github.com/mcuee/libusb-win32/releases/download/release_1.4.0.2/libusb-win32-bin-1.4.0.2.zip' 'libusb0.zip' '00004c92cdb99be36e17fb2377165eb97e63b48ba895bfc04a642ea9c3e26d94'
$usbk = Fetch 'https://github.com/mcuee/libusbk/releases/download/V3.1.0.0/libusbK-3.1.0.0-bin.7z' 'libusbk.7z' '38605d8d5a86f408a4b7bec60f6d4a096050eee72f89a63a8d5be125252d3fe7'
tar -xzf $source -C $work
if ($LASTEXITCODE) { throw 'Cannot extract libwdi' }
Expand-Archive $usb0 -DestinationPath $work -Force
& 7z x $usbk "-o$work/usbk" -y | Out-Null
if ($LASTEXITCODE) { throw 'Cannot extract libusbK' }
$src = Join-Path $work 'libwdi-1.5.1'
$kDll = Get-ChildItem "$work/usbk" -Recurse -Filter libusbK.dll | Where-Object { $_.Directory.Name -eq 'amd64' -and $_.Directory.Parent.Name -eq 'dll' } | Select-Object -First 1
if (-not $kDll) { throw 'libusbK DLL directory missing' }
$kDir = $kDll.Directory.Parent.Parent.FullName.Replace('\', '/')
$zeroDir = (Join-Path $work 'libusb-win32-bin-1.4.0.2').Replace('\', '/')
$config = Get-Content "$src/msvc/config.h" -Raw
$config = $config -replace '(?m)^#define WDK_DIR .*$', '// No WinUSB coinstallers needed'
$config = $config -replace '(?m)^#define LIBUSB0_DIR .*$', ('#define LIBUSB0_DIR "' + $zeroDir + '"')
$config = $config -replace '(?m)^#define LIBUSBK_DIR .*$', ('#define LIBUSBK_DIR "' + $kDir + '"')
$config = $config -replace '(?m)^#define OPT_ARM.*$', '// This installer build targets x86/x64'
$config = $config -replace '#define WDF_VER 1011', '#define WDF_VER 1009'
Set-Content "$src/msvc/config.h" $config -Encoding utf8
# libwdi's libusbK resource paths omit the leading zero used by the actual DLL
# names. Keep WDF_VER decimal: it is also used in arithmetic for INF generation.
$resources = "$src/libwdi/embedder_files.h"
(Get-Content $resources -Raw).Replace('WdfCoInstaller" STR(WDF_VER)', 'WdfCoInstaller0" STR(WDF_VER)') | Set-Content $resources -Encoding utf8
# Remove the ARM64 build dependency; the x86 helper embeds x64 installer support.
$proj = "$src/libwdi/.msvc/libwdi_static.vcxproj"
$xml = Get-Content $proj -Raw
$xml = $xml -replace '(?s)\s*<ProjectReference Include="installer_arm64.vcxproj">.*?</ProjectReference>', ''
Set-Content $proj $xml -Encoding utf8
$solution = "$src/libwdi.sln"
Get-Content $solution | Where-Object {
    $_ -notmatch '(?i)\{(?:6AC16F78-F266-4AE0-BD63-550A55F54C15|F7F7842F-2912-454E-ADF5-0B22987946E2)\}.*\.Build\.0\s*='
} | Set-Content "$solution.updated" -Encoding utf8
Move-Item "$solution.updated" $solution -Force
Copy-Item "$PSScriptRoot/tigard_usb_driver.c" "$src/examples/wdi-simple.c"
# Request elevation only for --install, from the setup script. --check is read-only.
$project = "$src/examples/.msvc/wdi-simple.vcxproj"
(Get-Content $project -Raw).Replace('RequireAdministrator', 'AsInvoker') | Set-Content $project -Encoding utf8
& msbuild $solution /t:Build /p:Configuration=Release /p:Platform=Win32 /m /v:minimal
if ($LASTEXITCODE) { throw 'Driver helper build failed' }
Copy-Item "$src/Win32/Release/examples/wdi-simple.exe" "$Output/tigard-usb-driver.exe"
Copy-Item "$src/COPYING" "$Output/LIBWDI-COPYING.txt"
Copy-Item "$src/COPYING-LGPL" "$Output/LIBWDI-LGPL.txt"
Copy-Item "$root/docs/WINDOWS_USB.md" "$Output/README.md"
Copy-Item $source "$Output/libwdi-1.5.1-source.tar.gz"
Copy-Item "$PSScriptRoot/build_driver.ps1", "$PSScriptRoot/tigard_usb_driver.c" $Output
& "$Output/tigard-usb-driver.exe" --help
if ($LASTEXITCODE) { throw 'Driver helper smoke test failed' }
Get-FileHash "$Output/tigard-usb-driver.exe" -Algorithm SHA256 | Format-List
