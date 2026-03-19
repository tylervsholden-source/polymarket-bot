"""
Proje zip'i oluşturur. Önce bozuk zip'i sil, sonra çalıştır:
  del polymarket_bot_v2.zip
  python make_zip.py
"""
import zipfile, os, pathlib

ROOT = pathlib.Path(__file__).parent
OUT  = ROOT / "polymarket_bot_v2.zip"

EXCLUDE_DIRS  = {".venv", "__pycache__", ".git", "node_modules", ".pytest_cache"}
EXCLUDE_FILES = {".env", "polymarket_bot_v2.zip", "make_zip.py", "dump.txt"}

# Sadece kanit loglarini dahil et (geri kalan loglar cok buyuk)
INCLUDE_LOGS = {"test_results_20260315.log", "runtime_evidence_20260315.log"}

def _should_include(path: pathlib.Path) -> bool:
    parts = path.parts
    if any(d in EXCLUDE_DIRS for d in parts):
        # logs/ klasoru: sadece kanit dosyalarini al
        if "logs" in parts:
            return path.name in INCLUDE_LOGS
        return False
    if path.name in EXCLUDE_FILES:
        return False
    return True

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if not _should_include(path):
            continue
        arcname = path.relative_to(ROOT)
        zf.write(path, arcname)
        print(f"  + {arcname}")

size = OUT.stat().st_size / 1024
print(f"\nOluşturuldu: {OUT.name} ({size:.0f} KB)")
