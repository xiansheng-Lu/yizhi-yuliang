$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $projectRoot '.runtime'
$version = '24.19.0'
New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
$nodePath = Join-Path $runtimePath 'node.exe'
if ((Test-Path -LiteralPath $nodePath) -and ((& $nodePath --version) -eq "v$version")) { Write-Output "Project Node v$version ready"; return }
$archiveName = "node-v$version-win-x64.zip"
$releaseUrl = "https://nodejs.org/dist/v$version"
$archivePath = Join-Path $runtimePath $archiveName
Invoke-WebRequest "$releaseUrl/$archiveName" -OutFile $archivePath
$checksums = (Invoke-WebRequest "$releaseUrl/SHASUMS256.txt").Content
$expected = (($checksums -split "`n" | Where-Object { $_.Trim().EndsWith(" $archiveName") }) -split '\s+')[0]
if (-not $expected -or (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Node archive checksum mismatch' }
Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimePath -Force
Copy-Item -LiteralPath (Join-Path $runtimePath "node-v$version-win-x64/node.exe") -Destination $nodePath
Write-Output "Project Node $(& $nodePath --version) ready. System PATH unchanged."
