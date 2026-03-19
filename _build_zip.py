"""Build project_snapshot.zip for ChatGPT review."""
import zipfile
import shutil
from pathlib import Path

PROJ = Path("c:/Users/lcladm/.antigravity/Polymarket")
ZIP_PATH = PROJ / "project_snapshot.zip"

# Directories/files to include with their destination in zip
INCLUDES = [
    # Root files
    ("README.md",              "project_snapshot/README.md"),
    ("requirements.txt",       "project_snapshot/requirements.txt"),
    ("PACKAGE_MANIFEST.md",    "project_snapshot/PACKAGE_MANIFEST.md"),
    ("CLAUDE.md",              "project_snapshot/CLAUDE.md"),
    # Root spec files
    ("BRIDGE_SPEC.md",             "project_snapshot/docs/BRIDGE_SPEC.md"),
    ("CALIBRATION_SPEC.md",        "project_snapshot/docs/CALIBRATION_SPEC.md"),
    ("DRIFT_MONITORING_SPEC.md",   "project_snapshot/docs/DRIFT_MONITORING_SPEC.md"),
    ("EDGE_ESTIMATION.md",         "project_snapshot/docs/EDGE_ESTIMATION.md"),
    ("EXECUTABLE_NOTIONAL_SPEC.md","project_snapshot/docs/EXECUTABLE_NOTIONAL_SPEC.md"),
    ("FIXES.md",                   "project_snapshot/docs/FIXES.md"),
    ("JOURNAL_SCHEMA.md",          "project_snapshot/docs/JOURNAL_SCHEMA.md"),
    ("MARKET_MAPPING.md",          "project_snapshot/docs/MARKET_MAPPING.md"),
    ("PAPER_MODE_POLICY.md",       "project_snapshot/docs/PAPER_MODE_POLICY.md"),
    ("PARTIAL_FILL_POLICY.md",     "project_snapshot/docs/PARTIAL_FILL_POLICY.md"),
    ("PRICING_SANITY_SPEC.md",     "project_snapshot/docs/PRICING_SANITY_SPEC.md"),
    ("SHADOW_RUNNER_SPEC.md",      "project_snapshot/docs/SHADOW_RUNNER_SPEC.md"),
    # calibration specs
    ("calibration/CONTRACT_HARDENING_SPEC.md",  "project_snapshot/docs/CONTRACT_HARDENING_SPEC.md"),
    ("calibration/DECISION_FLOW.md",            "project_snapshot/docs/DECISION_FLOW.md"),
    ("calibration/DECISION_INTEGRATION_SPEC.md","project_snapshot/docs/DECISION_INTEGRATION_SPEC.md"),
    ("calibration/HARDENING_SPEC.md",           "project_snapshot/docs/HARDENING_SPEC.md"),
    ("calibration/LIVE_VS_PAPER_POLICY.md",     "project_snapshot/docs/LIVE_VS_PAPER_POLICY.md"),
    ("calibration/SPEC.md",                     "project_snapshot/docs/CALIBRATION_FULL_SPEC.md"),
    # execution_realism specs
    ("execution_realism/COST_MODEL.md",            "project_snapshot/docs/COST_MODEL.md"),
    ("execution_realism/EXECUTION_REALISM_SPEC.md","project_snapshot/docs/EXECUTION_REALISM_SPEC.md"),
]

# Directory trees to include
DIR_INCLUDES = [
    ("config",             "project_snapshot/config"),
    ("signal_bridge",      "project_snapshot/signal_bridge"),
    ("calibration",        "project_snapshot/calibration"),
    ("execution_realism",  "project_snapshot/execution_realism"),
    ("shadow_runner",      "project_snapshot/shadow_runner"),
    ("monitoring",         "project_snapshot/monitoring"),
    ("tests",              "project_snapshot/tests"),
    ("artifacts",          "project_snapshot/artifacts"),
    ("docs",               "project_snapshot/docs/project_docs"),
]

# Patterns to skip
SKIP_PATTERNS = {
    "__pycache__", ".pytest_cache", ".venv", ".git",
    "node_modules", ".env", "*.pyc", "*.pyo",
    "_gen_artifacts.py", "_build_zip.py",
}

def should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_PATTERNS:
            return True
    if path.suffix in {".pyc", ".pyo"}:
        return True
    if path.name.startswith(".env"):
        return True
    return False


with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    # Individual files
    for src_rel, dst in INCLUDES:
        src = PROJ / src_rel
        if src.exists():
            zf.write(src, dst)
            print(f"  + {dst}")
        else:
            print(f"  MISSING: {src_rel}")

    # Directory trees
    for src_rel, dst_prefix in DIR_INCLUDES:
        src_dir = PROJ / src_rel
        if not src_dir.exists():
            print(f"  MISSING DIR: {src_rel}")
            continue
        for f in src_dir.rglob("*"):
            if f.is_file() and not should_skip(f):
                rel = f.relative_to(PROJ)
                arc = f"project_snapshot/{rel}".replace("\\", "/")
                zf.write(f, arc)

    total = len(zf.namelist())

print(f"\nZip created: {ZIP_PATH}")
print(f"Total files: {total}")
size_mb = ZIP_PATH.stat().st_size / 1024 / 1024
print(f"Size: {size_mb:.2f} MB")

# Print summary by top-level folder
from collections import Counter
folders = Counter()
for name in zf.namelist():
    parts = name.split("/")
    if len(parts) >= 3:
        folders[parts[1]] += 1
    elif len(parts) == 2:
        folders["(root)"] += 1

for folder, count in sorted(folders.items()):
    print(f"  {folder}/: {count} files")
