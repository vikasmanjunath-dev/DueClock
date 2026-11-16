# PyInstaller spec: one folder with two small executables sharing one runtime (console and windowed).
# Build from the repository root:  pyinstaller packaging/windows/dueclock-connector.spec --noconfirm
# Web-framework and test packages are excluded on purpose: the connector is a plain client. pydantic stays: SyncRecord validates the upload payload.
import os
ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
EXCLUDES = ["fastapi", "starlette", "uvicorn", "pytest", "psycopg2", "tkinter", "unittest", "numpy", "pandas", "matplotlib"]
HIDDEN = ["dueclock.connector.cli", "dueclock.core.ingest", "dueclock.core.tally_parse"]

a = Analysis([os.path.join(SPECPATH, "entry.py")], pathex=[ROOT], hiddenimports=HIDDEN, excludes=EXCLUDES, noarchive=False)
pyz = PYZ(a.pure)

console = EXE(pyz, a.scripts, [], exclude_binaries=True, name="dueclock-connector", console=True, version=os.path.join(SPECPATH, "version_info.txt"))
windowed = EXE(pyz, a.scripts, [], exclude_binaries=True, name="dueclock-connectorw", console=False, version=os.path.join(SPECPATH, "version_info.txt"))
coll = COLLECT(console, windowed, a.binaries, a.datas, name="dueclock-connector")
