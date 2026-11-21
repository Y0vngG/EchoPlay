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
from urllib.error import HTTPError, URLError
from zipfile import ZipFile, ZipInfo

from echoplay.services.updater import (
    INSTALL_SCRIPT, Release, check_release, extract_payload, prepare_update, version_tuple,
    _check_release_from_downloads,
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
            contents = (root / "dist" / RELEASE_ASSET).read_bytes()
            self.assertEqual((root / "dist" / "SHA256SUMS.txt").read_text(),
                             hashlib.sha256(contents).hexdigest() + "  " + RELEASE_ASSET + "\n")

    def fallback_responses(self, tag="v0.2.4", checksum=None, size="100", latest_url=None):
        latest = io.BytesIO()
        latest.geturl = lambda: latest_url or f"https://github.com/Y0vngG/EchoPlay/releases/tag/{tag}"
        sums = io.BytesIO(checksum if checksum is not None else
                         ("a" * 64 + "  EchoPlay-Windows.zip\n").encode())
        archive = io.BytesIO()
        archive.headers = {"Content-Length": size}
        return [latest, sums, archive]

    def test_rate_limit_and_network_failure_use_verified_release_downloads(self):
        errors = [HTTPError("https://api.github.com", code, "unavailable", {}, None)
                  for code in (403, 429, 503)] + [URLError("network down"), TimeoutError()]
        for error in errors:
            with self.subTest(error=error), patch("echoplay.services.updater.urlopen",
                    side_effect=[error, *self.fallback_responses()]) as open_url:
                release = check_release("0.2.3")
                self.assertEqual(release.version, "0.2.4")
                self.assertEqual(release.sha256, "a" * 64)
                self.assertEqual(release.size, 100)
                self.assertIn("/download/v0.2.4/", release.url)
                calls = open_url.call_args_list
                self.assertTrue(calls[2].args[0].full_url.endswith("/v0.2.4/SHA256SUMS.txt"))
                self.assertEqual(calls[3].args[0].method, "HEAD")

    def test_fallback_same_or_older_version_does_not_download_assets(self):
        for tag in ("v0.2.3", "v0.2.2"):
            with self.subTest(tag=tag), patch("echoplay.services.updater.urlopen",
                    side_effect=self.fallback_responses(tag=tag)) as open_url:
                self.assertIsNone(_check_release_from_downloads("0.2.3"))
                self.assertEqual(open_url.call_count, 1)

    def test_fallback_rejects_untrusted_tag_checksum_and_size(self):
        invalid = [
            {"latest_url": "https://evil.example/releases/tag/v0.2.4"},
            {"tag": "v0.2.4-beta"},
            {"checksum": b"not a checksum\n"},
            {"checksum": ("a" * 64 + "  another.zip\n").encode()},
            {"checksum": (("a" * 64 + "  EchoPlay-Windows.zip\n") * 2).encode()},
            {"checksum": b"a" * 65537},
            {"size": "0"}, {"size": "999999999999"},
        ]
        for options in invalid:
            with self.subTest(options=options), patch("echoplay.services.updater.urlopen",
                    side_effect=self.fallback_responses(**options)):
                with self.assertRaises(ValueError):
                    _check_release_from_downloads("0.2.3")

    def test_api_not_found_does_not_mask_error_with_fallback(self):
        with patch("echoplay.services.updater.urlopen",
                   side_effect=HTTPError("https://api.github.com", 404, "Not found", {}, None)) as open_url:
            with self.assertRaises(HTTPError):
                check_release("0.2.3")
            self.assertEqual(open_url.call_count, 1)

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
