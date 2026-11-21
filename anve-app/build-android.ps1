# Build the Android app (an installable .apk) on Windows.
#
#   powershell -ExecutionPolicy Bypass -File build-android.ps1 [-Clean]
#
# The Android C++ build tools fail when the project folder's path is long (Windows'
# 260-character limit; the tools repeat the folder path inside their own file names), so
# the build does not run here: the source is copied to the shortest possible work folder
# (C:\a, safe to delete at any time), built there, and the finished app is copied back to
# dist\anve-home.apk  next to this script. Even C:\anve\app was too long (tried).
# Needs: Node 20+, a JDK 17, and the Android SDK (it downloads missing SDK parts itself).
param([switch]$Clean, [string]$Work = 'C:\a', [string]$Abis = 'arm64-v8a,x86_64')
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$env:CI = '1'
$env:NODE_ENV = 'production'
if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk" }
$jdk = Get-ChildItem 'C:\Program Files\Eclipse Adoptium' -Directory -Filter 'jdk-17*' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
if ($jdk) { $env:JAVA_HOME = $jdk.FullName }

New-Item -ItemType Directory -Force $Work | Out-Null
$lockBefore = if (Test-Path "$Work\package-lock.json") { (Get-FileHash "$Work\package-lock.json").Hash } else { '' }
$cfgBefore = if (Test-Path "$Work\app.json") { (Get-FileHash "$Work\app.json").Hash } else { '' }
# Full paths on purpose: a bare name ("android") would also skip modules\anve-wifi\android,
# the app's own native module (that happened: the module silently went missing).
$skip = @('node_modules', 'android', 'ios', '.expo', 'dist') | ForEach-Object { "$src\$_"; "$Work\$_" }
robocopy $src $Work /MIR /XD $skip /XF *.log /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copying the source failed (robocopy $LASTEXITCODE)" }
Set-Location $Work

if ($Clean -or -not (Test-Path node_modules) -or $lockBefore -ne (Get-FileHash package-lock.json).Hash) {
  Write-Host '== installing packages'
  npm ci --no-audit --no-fund 2>&1 | Select-Object -Last 2
}
if ($Clean -or -not (Test-Path android) -or $cfgBefore -ne (Get-FileHash app.json).Hash) {
  Write-Host '== generating the Android project'
  cmd /c "npx expo prebuild --platform android --clean --no-install 2>&1" | Select-Object -Last 3
  "sdk.dir=$($env:ANDROID_HOME -replace '\\','/')" | Set-Content -Encoding ascii android\local.properties
}
Write-Host '== building (the first time takes 10-20 minutes)'
Set-Location android
cmd /c ".\gradlew.bat assembleRelease -PreactNativeArchitectures=$Abis --console=plain --no-daemon > `"$Work\build.log`" 2>&1"
$code = $LASTEXITCODE
Copy-Item "$Work\build.log" "$src\build.log" -Force
if ($code -ne 0) {
  Write-Host "BUILD FAILED (see build.log)"
  Select-String -Path "$Work\build.log" -Pattern 'What went wrong' -Context 0, 6 | Select-Object -First 2 | Out-String | Write-Host
  exit 1
}
$apk = Get-ChildItem "$Work\android\app\build\outputs\apk\release" -Filter *.apk | Select-Object -First 1
New-Item -ItemType Directory -Force "$src\dist" | Out-Null
Copy-Item $apk.FullName "$src\dist\anve-home.apk" -Force
Write-Host ("BUILD OK: dist\anve-home.apk  {0:N1} MB" -f ($apk.Length / 1MB))
