param(
    [string]$PythonExe = 'C:\Users\komi\Documents\Codex\2026-10-09\d-pvzol-ol-5\work\ui_env\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$TaskRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$EntryPath = Join-Path $TaskRoot 'staged\ui_shell.py'
$ManifestPath = Join-Path $TaskRoot 'staged\launcher.manifest'
$BuildPath = Join-Path $TaskRoot 'build'
$DistPath = Join-Path $TaskRoot 'dist'
$IconPath = 'D:\pvzol本地最新最新ol\PvZOL.ico'
foreach ($FilePath in @($PythonExe, $EntryPath, $ManifestPath, $IconPath)) {
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) { throw "Missing build input: $FilePath" }
}
New-Item -ItemType Directory -Path $BuildPath, $DistPath -Force | Out-Null
& $PythonExe -m PyInstaller --noconfirm --clean --onedir --noconsole `
    --name PvZLauncherUI --icon $IconPath --manifest $ManifestPath `
    --distpath $DistPath --workpath (Join-Path $BuildPath 'ui') --specpath $BuildPath `
    --collect-all webview --collect-all pythonnet --collect-all clr_loader `
    --hidden-import webview.platforms.edgechromium `
    --hidden-import webview.platforms.winforms --hidden-import clr $EntryPath
if ($LASTEXITCODE -ne 0) { throw "UI build failed ($LASTEXITCODE)" }
$PackagePath = Join-Path $DistPath 'PvZLauncherUI'
$ExePath = Join-Path $PackagePath 'PvZLauncherUI.exe'
foreach ($DllName in @('Python.Runtime.dll','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll')) {
    if (-not (Get-ChildItem -LiteralPath $PackagePath -Filter $DllName -Recurse -File)) { throw "Dependency missing: $DllName" }
}
$CheckProcess = Start-Process -FilePath $ExePath -ArgumentList '--self-test' -WorkingDirectory $PackagePath -WindowStyle Hidden -Wait -PassThru
if ($CheckProcess.ExitCode -ne 0) { throw "Frozen dependency check failed ($($CheckProcess.ExitCode))" }
Write-Output "Validated package: $PackagePath"
