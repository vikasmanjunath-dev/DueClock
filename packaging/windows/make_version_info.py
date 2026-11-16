"""Write the Windows version resource (file properties) from CONNECTOR_VERSION so the exe is identifiable and the version has one source."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from dueclock.connector import CONNECTOR_VERSION  # noqa: E402


def render(version: str) -> str:
    parts = (version.split(".") + ["0", "0", "0"])[:3] + ["0"]
    t = ", ".join(parts)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({t}), prodvers=({t}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'DueClock'),
      StringStruct('FileDescription', 'DueClock Tally connector (read-only)'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('ProductName', 'DueClock Connector'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ])
"""


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "version_info.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(CONNECTOR_VERSION))
    print("wrote", out)
