import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from echoplay.services.updater import (
    INSTALL_SCRIPT, Release, check_release, extract_payload, prepare_update, version_tuple,
)
from echoplay.version import APP_NAME, RELEASE_ASSET, VERSION
from build_support import package_portable


def package(version="0.2.4", extra=None):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("EchoPlay/update.json", json.dumps({
            "version": version, "executable": f"EchoPlay - {version}.exe"}))
        archive.writestr(f"EchoPlay/EchoPlay - {version}.exe", b"new executable")
        archive.writestr("EchoPlay/_internal/runtime.dll", b"runtime")
        if extra:
            entry = ZipInfo()
            entry.filename = extra
            archive.writestr(entry, "bad")
    return stream.getvalue()


class UpdateTests(unittest.TestCase):
    def test_packager_writes_matching_manifest_and_stable_asset_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "dist" / APP_NAME
            (folder / "_internal").mkdir(parents=True)
            (folder / (APP_NAME + ".exe")).write_bytes(b"test")
            (folder / "_internal" / "runtime.dll").write_bytes(b"test")
            with patch.object(package_portable, "__file__", str(root / "build_support" / "package_portable.py")):
                package_portable.main()
            payload = extract_payload(root / "dist" / RELEASE_ASSET, root / "extracted", VERSION)
            self.assertTrue((payload / (APP_NAME + ".exe")).exists())

    def release_data(self, version="v0.2.4"):
        return {"tag_name": version, "assets": [{
            "name": "EchoPlay-Windows.zip", "state": "uploaded", "size": 100,
            "digest": "sha256:" + "a" * 64,
            "browser_download_url": "https://github.com/Y0vngG/EchoPlay/releases/download/v0.2.4/EchoPlay-Windows.zip",
        }]}

    def check(self, data):
        with patch("echoplay.services.updater.urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
            return check_release("0.2.3")

    def test_version_comparison_and_stable_releases(self):
        self.assertGreater(version_tuple("v0.2.10"), version_tuple("0.2.9"))
        for value in ("v0.2.3", "0.2.2"):
            self.assertIsNone(self.check(self.release_data(value)))
        for key in ("draft", "prerelease"):
            data = self.release_data()
            data[key] = True
            self.assertIsNone(self.check(data))
        self.assertEqual(self.check(self.release_data()).version, "0.2.4")
        with self.assertRaises(ValueError):
            version_tuple("0.3.0-beta")

    def test_missing_asset_digest_and_untrusted_url_are_rejected(self):
        cases = [dict(assets=[])]
        for override in ({"digest": None}, {"browser_download_url": "https://evil.example/update.zip"},
                         {"size": -1}):
            data = self.release_data()
            data["assets"][0].update(override)
            cases.append(data)
        cases[0]["tag_name"] = "0.2.4"
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.check(data)

    def test_download_verification_shortcut_preservation_and_cleanup(self):
        contents = package()
        release = Release("0.2.4", "https://github.com/asset", len(contents),
                          hashlib.sha256(contents).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "installed"
            target.mkdir()
            old_exe = target / "EchoPlay - 0.2.3.exe"
            old_exe.write_bytes(b"old")
            with patch("echoplay.services.updater.urlopen", return_value=io.BytesIO(contents)):
                update = prepare_update(release, old_exe)
            self.assertEqual(old_exe.read_bytes(), b"old")
            self.assertEqual((update.payload / old_exe.name).read_bytes(), b"new executable")
            self.assertEqual(json.loads((update.payload / "update.json").read_text())["executable"], old_exe.name)
            self.assertFalse((update.work / "release.zip").exists())
            before = set(Path(directory).iterdir())
            bad = Release("0.2.4", release.url, release.size, "0" * 64)
            for data in (contents, contents[:-2], contents + b"bad"):
                with patch("echoplay.services.updater.urlopen", return_value=io.BytesIO(data)):
                    with self.assertRaises(ValueError):
                        prepare_update(bad, old_exe)
                self.assertEqual(set(Path(directory).iterdir()), before)

    def test_unsafe_zip_entries_and_version_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            archive = directory / "test.zip"
            for name in ("../escape", "/absolute", "C:/escape", "EchoPlay/../escape",
                         "EchoPlay/a:stream", "EchoPlay/CON", "EchoPlay/test. ",
                         "EchoPlay\\..\\escape", "EchoPlay/UPDATE.JSON"):
                archive.write_bytes(package(extra=name))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    extract_payload(archive, directory / "out", "0.2.4")
                self.assertFalse((directory / "out").exists())
            archive.write_bytes(package())
            with self.assertRaisesRegex(ValueError, "版本"):
                extract_payload(archive, directory / "out", "0.2.5")

    @unittest.skipUnless(sys.platform == "win32", "Windows installer")
    def test_real_powershell_swap_and_rollback_with_unicode_and_spaces(self):
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory(prefix="更新 ' ") as directory:
                root = Path(directory)
                target, work, backup = root / "播放器", root / "work", root / "backup"
                payload = work / "unpacked"
                target.mkdir()
                payload.mkdir(parents=True)
                (target / "old.txt").write_text("preserved")
                (target / "EchoPlay.exe").write_bytes(b"invalid old exe")
                (payload / "new.txt").write_text("updated")
                exe = payload / "EchoPlay.exe"
                if fail:
                    exe.write_bytes(b"invalid exe")
                else:
                    exe.write_bytes((Path(os.environ["SystemRoot"]) / "System32/whoami.exe").read_bytes())
                (work / "install.json").write_text(json.dumps(dict(
                    target=str(target), payload=str(payload), backup=str(backup),
                    executable="EchoPlay.exe", pid=2147483647)), encoding="utf-8")
                script = work / "install.ps1"
                script.write_text(INSTALL_SCRIPT, encoding="utf-8-sig")
                result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive",
                                         "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                        capture_output=True, timeout=20)
                if fail:
                    self.assertTrue((target / "old.txt").exists(), result.stderr)
                    self.assertTrue((work / "error.log").exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue((target / "new.txt").exists())
                    self.assertTrue((backup / "old.txt").exists())
                    # Start-Process returns before the short-lived EXE unlocks.
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            (target / "EchoPlay.exe").unlink()
                            break
                        except PermissionError:
                            if time.monotonic() > deadline:
                                raise
                            time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
