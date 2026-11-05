# Installs Orbit for the current Windows user. No administrator rights are needed.
# Run it with "Install Orbit.cmd" from the unzipped folder.
param([switch]$Quiet)
$ErrorActionPreference = 'Stop'

function Ensure-Key([string]$Path) {
    # create the key only when missing, so existing values are never lost
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -Path $Path -Force | Out-Null }
}

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $env:LOCALAPPDATA 'Programs\Orbit'
$pyw = Join-Path $dest 'python\pythonw.exe'
$script = Join-Path $dest 'app\orbit_server.py'
$icon = Join-Path $dest 'Orbit.ico'
$runArgs = '"' + $script + '" --exit-when-idle 45'

Write-Host ''
Write-Host 'Installing Orbit to' $dest

# stop a running copy so its files can be replaced
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dest) } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

if ((Resolve-Path -LiteralPath $src).Path.TrimEnd('\') -ne $dest.TrimEnd('\')) {
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem -LiteralPath $src | Copy-Item -Destination $dest -Recurse -Force
}
# files from a downloaded zip carry the "came from the internet" mark; clear it
Get-ChildItem -LiteralPath $dest -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $pyw)) { throw "Python is missing from the package: $pyw" }

# Start menu and desktop shortcuts
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir 'Orbit.lnk'))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = $runArgs
    $lnk.WorkingDirectory = $dest
    $lnk.IconLocation = "$icon,0"
    $lnk.Description = 'Replay drone flights against radar and weather'
    $lnk.Save()
}

# "Open with > Orbit" for data files. Orbit never becomes the default app for them.
$cls = 'HKCU:\Software\Classes'

# The earlier Orbit (the KML 3D viewer) installed into the same folder and is replaced now,
# so remove its .kml/.kmz file links, which would point to a file that no longer exists.
foreach ($ext in @('.kml', '.kmz')) {
    $oldProg = 'Orbit' + $ext
    if (Test-Path -LiteralPath "$cls\$oldProg") { Remove-Item -LiteralPath "$cls\$oldProg" -Recurse -Force }
    if (Test-Path -LiteralPath "$cls\$ext\OpenWithProgids") {
        Remove-ItemProperty -LiteralPath "$cls\$ext\OpenWithProgids" -Name $oldProg -ErrorAction SilentlyContinue
    }
    $rk = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Software\Classes\$ext", $true)
    if ($rk) {
        if ($rk.GetValue('') -eq $oldProg) { $rk.DeleteValue('', $false) }
        $rk.Close()
    }
}

$prog = "$cls\Orbit.Data"
Ensure-Key "$prog\DefaultIcon"
Ensure-Key "$prog\shell\open\command"
Set-ItemProperty -LiteralPath $prog -Name '(default)' -Value 'Orbit data file'
Set-ItemProperty -LiteralPath "$prog\DefaultIcon" -Name '(default)' -Value "$icon,0"
Set-ItemProperty -LiteralPath "$prog\shell\open\command" -Name '(default)' -Value ('"' + $pyw + '" ' + $runArgs + ' "%1"')
# the name and icon "Open with" shows; without them Windows names the app after pythonw.exe ("Python")
Set-ItemProperty -LiteralPath "$prog\shell\open" -Name 'FriendlyAppName' -Value 'Orbit'
Ensure-Key "$prog\Application"
Set-ItemProperty -LiteralPath "$prog\Application" -Name 'ApplicationName' -Value 'Orbit'
Set-ItemProperty -LiteralPath "$prog\Application" -Name 'ApplicationIcon' -Value "$icon,0"
Set-ItemProperty -LiteralPath "$prog\Application" -Name 'ApplicationDescription' -Value 'Replay drone flights against radar and weather'
foreach ($ext in @('.txt', '.bin', '.tlog', '.xml', '.csv', '.xlsx', '.gpx', '.kml', '.kmz', '.json')) {
    Ensure-Key "$cls\$ext\OpenWithProgids"
    New-ItemProperty -LiteralPath "$cls\$ext\OpenWithProgids" -Name 'Orbit.Data' -Value '' -PropertyType String -Force | Out-Null
}

# Entry in Settings > Apps, with an uninstaller
$un = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Orbit'
Ensure-Key $un
$version = (Get-Content -LiteralPath (Join-Path $dest 'VERSION.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
Set-ItemProperty -LiteralPath $un -Name 'DisplayName' -Value 'Orbit'
Set-ItemProperty -LiteralPath $un -Name 'DisplayVersion' -Value ([string]$version)
Set-ItemProperty -LiteralPath $un -Name 'Publisher' -Value 'Orbit'
Set-ItemProperty -LiteralPath $un -Name 'DisplayIcon' -Value $icon
Set-ItemProperty -LiteralPath $un -Name 'InstallLocation' -Value $dest
Set-ItemProperty -LiteralPath $un -Name 'UninstallString' -Value ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $dest 'uninstall.ps1') + '"')
New-ItemProperty -LiteralPath $un -Name 'NoModify' -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -LiteralPath $un -Name 'NoRepair' -Value 1 -PropertyType DWord -Force | Out-Null

Write-Host ''
Write-Host 'Orbit is installed. Start it from the Start menu or the desktop icon.'
Write-Host 'It opens in a Microsoft Edge app window. Settings, saved maps and live recordings are kept in' (Join-Path $env:LOCALAPPDATA 'Orbit')
if (-not $Quiet) { Start-Process -FilePath $pyw -ArgumentList $runArgs -WorkingDirectory $dest }
