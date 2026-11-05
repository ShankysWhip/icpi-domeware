# Removes Orbit for the current Windows user. Your settings, saved map tiles, DJI keys
# and live recordings in %LOCALAPPDATA%\Orbit are kept unless you delete that folder.
$ErrorActionPreference = 'Continue'
$dest = Join-Path $env:LOCALAPPDATA 'Programs\Orbit'

Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dest) } | Stop-Process -Force -ErrorAction SilentlyContinue

foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
    $lnk = Join-Path $dir 'Orbit.lnk'
    if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk -Force }
}
$cls = 'HKCU:\Software\Classes'
foreach ($ext in @('.txt', '.bin', '.tlog', '.xml', '.csv', '.xlsx', '.gpx', '.kml', '.kmz', '.json')) {
    if (Test-Path -LiteralPath "$cls\$ext\OpenWithProgids") {
        Remove-ItemProperty -LiteralPath "$cls\$ext\OpenWithProgids" -Name 'Orbit.Data' -ErrorAction SilentlyContinue
    }
}
if (Test-Path -LiteralPath "$cls\Orbit.Data") { Remove-Item -LiteralPath "$cls\Orbit.Data" -Recurse -Force }
$un = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Orbit'
if (Test-Path -LiteralPath $un) { Remove-Item -LiteralPath $un -Recurse -Force }

# the program folder holds this script, so remove it a moment after the script ends
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', ('timeout /t 2 /nobreak >nul & rmdir /s /q "' + $dest + '"') -WindowStyle Hidden
Write-Host 'Orbit was removed. Your data is still in' (Join-Path $env:LOCALAPPDATA 'Orbit')
Start-Sleep -Seconds 2
