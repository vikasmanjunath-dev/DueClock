# Build the connector installer on Windows. Run from the repository root in PowerShell:  .\packaging\windows\build.ps1
$ErrorActionPreference = "Stop"
python -m pip install -r requirements-connector-build.txt
python -m pytest tests/connector -q
if ($LASTEXITCODE -ne 0) { throw "connector tests failed" }
python packaging/windows/make_version_info.py
pyinstaller packaging/windows/dueclock-connector.spec --noconfirm --distpath dist --workpath build
$version = python -c "from dueclock.connector import CONNECTOR_VERSION as v; print(v)"
# Smoke: the frozen exe must start and report its version without a config, a console, or Tally.
$v = & .\dist\dueclock-connector\dueclock-connector.exe version
if ($v -notmatch $version) { throw "frozen exe did not report version $version (got: $v)" }
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$version" packaging\windows\installer.iss
if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
$setup = "dist\DueClockConnector-Setup-$version.exe"
(Get-FileHash $setup -Algorithm SHA256).Hash | Out-File "$setup.sha256" -Encoding ascii
Write-Host "Built $setup"
