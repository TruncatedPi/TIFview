# Download the checked portable release. Python/Git/admin access aren't required.
[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'TIFview'),
    [switch]$NoShortcut,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$headers = @{ 'User-Agent' = 'TIFview-Windows-setup'; 'Accept' = 'application/vnd.github+json' }
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/TruncatedPi/TIFview/releases/latest' -Headers $headers
if ($release.tag_name -notmatch '^v\d+\.\d+\.\d+$') { throw 'Unexpected release version.' }
$zipName = "TIFview-$($release.tag_name.Substring(1))-windows-x64.zip"
$zipAsset = @($release.assets | Where-Object name -EQ $zipName)
$hashAsset = @($release.assets | Where-Object name -EQ "$zipName.sha256")
if ($zipAsset.Count -ne 1 -or $hashAsset.Count -ne 1) { throw 'This release has no complete Windows download.' }

$root = [IO.Path]::GetFullPath($InstallRoot)
$versionFolder = Join-Path $root $release.tag_name
$executable = Join-Path $versionFolder 'TIFview\TIFview.exe'
if (!(Test-Path -LiteralPath $executable)) {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $download = Join-Path $root $zipName
    Write-Host "Downloading TIFview $($release.tag_name)..."
    Invoke-WebRequest -Uri $zipAsset[0].browser_download_url -OutFile $download -UseBasicParsing
    $hashDownload = "$download.sha256"
    Invoke-WebRequest -Uri $hashAsset[0].browser_download_url -OutFile $hashDownload -UseBasicParsing
    $hashText = Get-Content -LiteralPath $hashDownload -Raw
    $expected = ($hashText -split '\s+')[0]
    if ($expected -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $expected) {
        throw 'Download checksum failed; setup stopped.'
    }
    # A new version directory keeps updates separate from a running older build.
    if (Test-Path -LiteralPath $versionFolder) { throw "Incomplete setup exists at $versionFolder. Choose a new InstallRoot." }
    Expand-Archive -LiteralPath $download -DestinationPath $versionFolder
    if (!(Test-Path -LiteralPath $executable)) { throw 'The download did not contain TIFview.exe.' }
    Remove-Item -LiteralPath $download
    Remove-Item -LiteralPath $hashDownload
}
if (!$NoShortcut) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $desktop 'TIFview.lnk'))
    $shortcut.TargetPath = $executable
    $shortcut.WorkingDirectory = Split-Path -Parent $executable
    $shortcut.Description = "TIFview $($release.tag_name.Substring(1)) - printing image, channel and layer viewer"
    $shortcut.Save()
}
Write-Host "TIFview is ready: $executable"
if (!$NoLaunch) { Start-Process -FilePath $executable -WindowStyle Normal }
