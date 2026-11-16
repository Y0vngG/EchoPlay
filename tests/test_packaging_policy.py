import unittest
from types import SimpleNamespace
from unittest.mock import patch

from build_support.payload import exclusion_reason, trim_payload, validate_required_files
from build_support.payload import trim_unreferenced_qt_libraries


class PackagingPolicyTests(unittest.TestCase):
    def test_runtime_browser_audio_graphics_and_input_support_are_kept(self):
        required = (
            "PySide6/Qt6WebEngineCore.dll", "PySide6/QtWebEngineProcess.exe",
            "PySide6/resources/qtwebengine_resources.pak",
            "PySide6/resources/qtwebengine_devtools_resources.pak",
            "PySide6/resources/v8_context_snapshot.bin", "PySide6/resources/icudtl.dat",
            "PySide6/translations/qtwebengine_locales/zh-CN.pak",
            "PySide6/translations/qtwebengine_locales/ja.pak",
            "PySide6/plugins/multimedia/ffmpegmediaplugin.dll",
            "PySide6/avcodec-63.dll", "PySide6/Qt6Quick.dll", "PySide6/Qt6Qml.dll",
            "PySide6/opengl32sw.dll", "PySide6/d3dcompiler_47.dll",
            "PySide6/plugins/platforms/qwindows.dll",
            "PySide6/plugins/imageformats/qico.dll",
            "assets/excel_ribbon_dark.png", "python313.dll",
        )
        for name in required:
            with self.subTest(name=name):
                self.assertIsNone(exclusion_reason(name))

    def test_only_explicit_unused_groups_are_removed(self):
        unused = (
            "PySide6/resources/qtwebengine_devtools_resources.debug.pak",
            "PySide6/translations/qtbase_de.qm",
            "PySide6/plugins/qmltooling/qmldbg_debugger.dll",
            "PySide6/qml/QtQuick3D/Effects/designer/example.qml",
            "PySide6/qml/QtQuick/Controls/Windows/qmldir",
            "PySide6/Qt6Quick3DPhysics.dll", "PySide6/Qt6VirtualKeyboard.dll",
            "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
            "PySide6/plugins/imageformats/qpdf.dll",
            "PySide6/QtOpenGL.pyd", "PySide6/QtQuick.pyd", "PySide6/QtQml.pyd",
        )
        for name in unused:
            with self.subTest(name=name):
                self.assertIsNotNone(exclusion_reason(name.replace("/", "\\")))

    def test_trimming_keeps_original_records_and_order(self):
        entries = [("PySide6/Qt6Core.dll", "core.dll", "BINARY"),
                   ("PySide6/resources/a.debug.pak", "debug.pak", "DATA"),
                   ("assets/excel_ribbon.png", "image.png", "DATA")]
        kept, removed = trim_payload(entries)
        self.assertEqual(kept, [entries[0], entries[2]])
        self.assertEqual(removed, [entries[1]])
        self.assertEqual(len(entries), 3)

    def test_incomplete_runtime_stops_packaging(self):
        with self.assertRaisesRegex(RuntimeError, "required files"):
            validate_required_files([])

    def test_native_pruning_follows_plugin_and_transitive_dependencies(self):
        entries = [(f"PySide6/{name}", name, "BINARY") for name in
                   ("plugin.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QuickControls2.dll")]
        dependencies = {"plugin.dll": ["Qt6Quick.dll"], "Qt6Quick.dll": ["Qt6Qml.dll"]}
        def pe_file(name, **kwargs):
            result = SimpleNamespace(
                DIRECTORY_ENTRY_IMPORT=[],
                DIRECTORY_ENTRY_DELAY_IMPORT=[SimpleNamespace(dll=d.encode())
                                             for d in dependencies.get(name, [])],
                parse_data_directories=lambda **kwargs: None)
            context = unittest.mock.MagicMock()
            context.__enter__.return_value = result
            return context
        with patch("pefile.PE", side_effect=pe_file):
            kept, removed = trim_unreferenced_qt_libraries(entries)
        self.assertEqual(kept, entries[:3])
        self.assertEqual(removed, entries[3:])

    def test_duplicate_qt_and_its_unused_icu_dependency_are_removed(self):
        entries = [
            ("PySide6/Qt6Core.dll", "bundled-core", "BINARY"),
            ("Qt6Core.dll", "foreign-core", "BINARY"),
            ("icudt.dll", "unused-icu", "BINARY"),
            ("_ssl.pyd", "ssl-extension", "EXTENSION"),
            ("libssl.dll", "used-ssl", "BINARY"),
        ]
        dependencies = {"foreign-core": ["icudt.dll"], "ssl-extension": ["libssl.dll"]}
        def pe_file(name, **kwargs):
            context = unittest.mock.MagicMock()
            context.__enter__.return_value = SimpleNamespace(
                DIRECTORY_ENTRY_IMPORT=[SimpleNamespace(dll=d.encode())
                                        for d in dependencies.get(name, [])],
                parse_data_directories=lambda **kwargs: None)
            return context
        with patch("pefile.PE", side_effect=pe_file):
            kept, removed = trim_unreferenced_qt_libraries(entries)
        self.assertEqual(kept, [entries[0], *entries[3:]])
        self.assertEqual(removed, entries[1:3])
