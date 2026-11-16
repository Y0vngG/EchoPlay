# Build configuration for the portable Windows application folder.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from build_support.payload import trim_payload, validate_required_files, validate_native_dependencies
from build_support.payload import trim_unreferenced_qt_libraries
from build_support.payload import UNUSED_QT_BINDINGS
qt_plugin_categories = {"imageformats", "multimedia", "platforms", "styles"}
qt_plugin_binaries = [
    (source, destination)
    for source, destination in collect_dynamic_libs("PySide6")
    if Path(source).parent.parent.name == "plugins"
    and Path(source).parent.name in qt_plugin_categories
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=qt_plugin_binaries,
    datas=[
        (str(ROOT / "assets" / "app_icon.png"), "."),
        (str(ROOT / "assets" / "excel_ribbon.png"), "assets"),
        (str(ROOT / "assets" / "excel_ribbon_dark.png"), "assets"),
        (str(ROOT / "assets" / "taskbar_icons"), "assets/taskbar_icons"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=list(UNUSED_QT_BINDINGS),
    noarchive=False,
    optimize=0,
)
a.binaries, removed_binaries = trim_payload(a.binaries)
a.datas, removed_datas = trim_payload(a.datas)
a.binaries, unreferenced_libraries = trim_unreferenced_qt_libraries(a.binaries)
removed_binaries += unreferenced_libraries
validate_required_files([*a.binaries, *a.datas])
validate_native_dependencies(a.binaries, removed_binaries)
print(f"EchoPlay packaging: omitted {len(removed_binaries) + len(removed_datas)} unused/debug files")
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EchoPlay - 0.2.2",
    version=str(ROOT / "assets" / "version_info.txt"),
    icon=str(ROOT / "assets" / "app_icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EchoPlay - 0.2.2",
)
