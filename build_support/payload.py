"""Conservative packaging rules for the QWidget-based EchoPlay desktop app."""
from pathlib import PurePosixPath

UNUSED_QT_BINDINGS = ("PySide6.QtOpenGL", "PySide6.QtQuick", "PySide6.QtQml")


def exclusion_reason(destination):
    path = destination.replace("\\", "/").lower()
    name = PurePosixPath(path).name
    if path in {module.replace(".", "/").lower() + ".pyd" for module in UNUSED_QT_BINDINGS}:
        return "Unused Python Qt bindings (native Qt libraries are retained)"
    if path == "pyside6/plugins/imageformats/qpdf.dll":
        return "Unused PDF image loader"
    if path.startswith("pyside6/qml/"):
        # EchoPlay uses QWidget/QWebEngineView, never QQmlEngine or QQuickView.
        return "Unused QML interface resources and plugins"
    if path.startswith("pyside6/resources/") and name.endswith((".debug.pak", ".debug.bin")):
        return "Qt debug resources"
    if path.startswith("pyside6/translations/") and name.endswith(".qm"):
        # The app never installs a QTranslator. Browser locale .pak files stay.
        return "Unused Qt translation catalogs"
    if path.startswith("pyside6/plugins/qmltooling/"):
        return "QML debugging tools"
    roots = (
        "pyside6/qml/qtquick3d/", "pyside6/qml/qt3d/",
        "pyside6/qml/qtgraphs/", "pyside6/qml/qtquick/scene2d/",
        "pyside6/qml/qtquick/scene3d/", "pyside6/qml/qtquick/virtualkeyboard/",
        "pyside6/plugins/assetimporters/", "pyside6/plugins/geometryloaders/",
        "pyside6/plugins/renderplugins/", "pyside6/plugins/sceneparsers/",
    )
    if path.startswith(roots):
        return "Unused 3D/virtual-keyboard components"
    if path == "pyside6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll":
        return "Unused virtual-keyboard input plugin"
    if path.startswith("pyside6/") and name.endswith(".dll") and name.startswith(
        ("qt6quick3d", "qt63d", "qt6virtualkeyboard", "qt6graphs")
    ):
        return "Unused 3D/virtual-keyboard libraries"
    return None


def trim_payload(entries):
    kept, removed = [], []
    for entry in entries:
        if exclusion_reason(entry[0]):
            removed.append(entry)
        else:
            kept.append(entry)
    return kept, removed


def validate_required_files(entries):
    names = {entry[0].replace("\\", "/").lower() for entry in entries}
    required = (
        "pyside6/qt6core.dll", "pyside6/qt6gui.dll", "pyside6/qt6widgets.dll",
        "pyside6/qt6network.dll", "pyside6/qt6multimedia.dll",
        "pyside6/qt6quick.dll", "pyside6/qt6qml.dll", "pyside6/qt6webenginecore.dll",
        "pyside6/qtwebengineprocess.exe", "pyside6/opengl32sw.dll",
        "pyside6/plugins/platforms/qwindows.dll",
        "pyside6/plugins/multimedia/ffmpegmediaplugin.dll",
        "pyside6/plugins/imageformats/qico.dll",
        "pyside6/resources/icudtl.dat", "pyside6/resources/qtwebengine_resources.pak",
        "pyside6/resources/v8_context_snapshot.bin",
        "pyside6/translations/qtwebengine_locales/en-us.pak",
        "pyside6/translations/qtwebengine_locales/zh-cn.pak",
        "assets/excel_ribbon.png", "assets/excel_ribbon_dark.png",
    )
    missing = set(required) - names
    for prefix in ("avcodec-", "avformat-", "avutil-", "swresample-"):
        if not any(PurePosixPath(name).name.startswith(prefix) for name in names):
            missing.add(prefix + "*.dll")
    if missing:
        raise RuntimeError("Packaging removed required files: " + ", ".join(sorted(missing)))


def validate_native_dependencies(kept, removed):
    """Fail the build if a retained PE binary imports a removed library."""
    import pefile  # PyInstaller's build dependency; never imported by EchoPlay.
    removed_names = {PurePosixPath(e[0].replace("\\", "/")).name.lower()
                     for e in removed if e[0].lower().endswith((".dll", ".pyd"))}
    removed_names -= {PurePosixPath(e[0].replace("\\", "/")).name.lower() for e in kept}
    conflicts = []
    for destination, source, kind in kept:
        if kind not in ("BINARY", "EXTENSION"):
            continue
        with pefile.PE(source, fast_load=True) as pe:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
            ])
            imports = (list(getattr(pe, "DIRECTORY_ENTRY_IMPORT", []))
                       + list(getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])))
            for entry in imports:
                dependency = entry.dll.decode("ascii").lower()
                if dependency in removed_names:
                    conflicts.append(f"{destination} -> {dependency}")
    if conflicts:
        raise RuntimeError("Unsafe dependency pruning:\n" + "\n".join(conflicts))


def trim_unreferenced_qt_libraries(entries):
    """Keep libraries reachable from Python extensions and runtime plugins.

    QML plugins have already been removed. Preserve all other binaries as roots,
    including dynamically loaded platform, TLS, image and multimedia plugins.
    Root DLLs are traced as well to avoid keeping dependencies of unused plugins.
    """
    import pefile
    def basename(entry):
        return PurePosixPath(entry[0].replace("\\", "/")).name.lower()
    candidates = {basename(e) for e in entries
                  if e[0].replace("\\", "/").lower().startswith("pyside6/")
                  and basename(e).startswith("qt6") and basename(e).endswith(".dll")}
    # Collecting a plugin can pull a second Qt installation from PATH. Prefer
    # the bundled PySide6 copy and trace the remaining root DLLs from consumers.
    bundled = {basename(e) for e in entries if e[0].replace("\\", "/").lower().startswith("pyside6/")}
    duplicates = [e for e in entries if "/" not in e[0].replace("\\", "/")
                  and basename(e).startswith("qt6") and basename(e) in bundled]
    entries = [e for e in entries if e not in duplicates]
    runtime_prefixes = ("python", "vcruntime", "msvcp", "concrt", "ucrtbase", "d3d")
    candidates.update(basename(e) for e in entries
                      if "/" not in e[0].replace("\\", "/")
                      and basename(e).endswith(".dll")
                      and not basename(e).startswith(runtime_prefixes))
    dependencies = {}
    for entry in entries:
        if entry[2] not in ("BINARY", "EXTENSION"):
            continue
        with pefile.PE(entry[1], fast_load=True) as pe:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
            ])
            imports = (list(getattr(pe, "DIRECTORY_ENTRY_IMPORT", []))
                       + list(getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])))
            dependencies.setdefault(basename(entry), set()).update(
                item.dll.decode("ascii").lower() for item in imports)
    pending = list(set(dependencies) - candidates)
    # Qt may load these base libraries by name rather than through PE imports.
    pending += ["qt6core.dll", "qt6gui.dll", "qt6widgets.dll", "qt6network.dll",
                "qt6multimedia.dll", "qt6webenginecore.dll", "qt6webenginewidgets.dll"]
    reachable = set()
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending.extend(dependencies.get(name, ()))
    kept, removed = [], list(duplicates)
    unused = candidates - reachable
    for entry in entries:
        (removed if basename(entry) in unused else kept).append(entry)
    return kept, removed
