"""Verified GitHub release downloads and an out-of-process Windows folder swap."""
from dataclasses import dataclass
import hashlib
import json
import os
import ntpath
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile

from echoplay.version import RELEASE_ASSET, REPOSITORY, VERSION

MAX_ARCHIVE = 1024 * 1024 * 1024
MAX_UNPACKED = 3 * MAX_ARCHIVE


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise ValueError(f"不支持的正式版本号：{value}")
    return tuple(map(int, match.groups()))


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PreparedUpdate:
    work: Path
    payload: Path
    target: Path
    executable: str
    version: str


def _request(url):
    return Request(url, headers={"User-Agent": f"EchoPlay/{VERSION}",
                                 "Accept": "application/vnd.github+json"})


def check_release(current=VERSION) -> Release | None:
    with urlopen(_request(f"https://api.github.com/repos/{REPOSITORY}/releases/latest"),
                 timeout=15) as response:
        data = json.loads(response.read(2 * 1024 * 1024))
    if data.get("draft") or data.get("prerelease"):
        return None
    version = data["tag_name"]
    if version_tuple(version) <= version_tuple(current):
        return None
    asset = next((a for a in data.get("assets", [])
                  if a.get("name") == RELEASE_ASSET and a.get("state") == "uploaded"), None)
    if asset is None:
        raise ValueError("新版尚未上传 Windows 便携版。")
    url = asset["browser_download_url"]
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or not parsed.path.startswith(f"/{REPOSITORY}/releases/download/")):
        raise ValueError("更新下载地址不属于官方仓库。")
    digest = asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("GitHub 未提供更新包 SHA-256，已取消更新。")
    size = int(asset["size"])
    if not 0 < size <= MAX_ARCHIVE:
        raise ValueError("更新包大小异常。")
    return Release(version.lstrip("v"), url, size, digest[7:].lower())


def extract_payload(archive: Path, destination: Path, version: str) -> Path:
    """Validate every entry before writing, including Windows-specific paths."""
    with ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 30000 or sum(i.file_size for i in entries) > MAX_UNPACKED:
            raise ValueError("更新包解压大小异常。")
        seen = set()
        for entry in entries:
            name = entry.filename
            parts = name.rstrip("/").split("/")
            if (not parts or "\\" in name or PureWindowsPath(name).drive
                    or any(p in ("", ".", "..") or p.endswith((" ", "."))
                           or re.search(r'[<>:"|?*\x00-\x1f]', p)
                           or ntpath.isreserved(p) for p in parts)
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("更新包包含不安全路径。")
            key = name.rstrip("/").casefold()
            if key in seen:
                raise ValueError("更新包包含重复路径。")
            seen.add(key)
        source.extractall(destination)
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("更新包应包含唯一的应用文件夹。")
    root = roots[0]
    manifest = json.loads((root / "update.json").read_text(encoding="utf-8"))
    if version_tuple(manifest["version"]) != version_tuple(version):
        raise ValueError("更新包版本与 GitHub 标签不一致。")
    executable = manifest["executable"]
    if (not re.fullmatch(r"EchoPlay(?: - \d+\.\d+\.\d+)?\.exe", executable)
            or not (root / executable).is_file() or not (root / "_internal").is_dir()):
        raise ValueError("更新包缺少可执行文件或运行环境。")
    return root


def prepare_update(release: Release, executable: Path, progress=lambda message: None,
                   cancelled=lambda: False) -> PreparedUpdate:
    executable = executable.resolve()
    target = executable.parent
    # Use the same volume for atomic directory renames; fail here if read-only.
    work = Path(tempfile.mkdtemp(prefix=".echoplay-update-", dir=target.parent))
    try:
        if shutil.disk_usage(work).free < release.size * 5:
            raise OSError("磁盘空间不足，暂时无法更新。")
        archive = work / "release.zip"
        digest = hashlib.sha256()
        received = 0
        last_percent = -1
        with urlopen(_request(release.url), timeout=30) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                if cancelled():
                    raise RuntimeError("更新已取消。")
                received += len(chunk)
                if received > release.size:
                    raise ValueError("更新包大小超出预期。")
                output.write(chunk)
                digest.update(chunk)
                percent = received * 100 // release.size
                if percent != last_percent:
                    progress(f"正在下载 EchoPlay {release.version}：{percent}%")
                    last_percent = percent
        if received != release.size or digest.hexdigest() != release.sha256:
            raise ValueError("更新包大小或 SHA-256 校验失败。")
        progress("正在校验并解压更新包…")
        payload = extract_payload(archive, work / "unpacked", release.version)
        manifest = json.loads((payload / "update.json").read_text(encoding="utf-8"))
        new_exe = payload / manifest["executable"]
        if new_exe.name != executable.name:
            new_exe.rename(payload / executable.name)
        # Keep existing shortcuts pointing to the same executable path.
        manifest["executable"] = executable.name
        (payload / "update.json").write_text(json.dumps(manifest), encoding="utf-8")
        archive.unlink()
        return PreparedUpdate(work, payload, target, executable.name, release.version)
    except BaseException:
        shutil.rmtree(work)
        raise


# The helper runs outside the installed folder, waits for the parent to release
# DLLs, and restores the old folder if either the swap or process launch fails.
INSTALL_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$target = [IO.Path]::GetFullPath($config.target)
$payload = [IO.Path]::GetFullPath($config.payload)
$backup = [IO.Path]::GetFullPath($config.backup)
$work = [IO.Path]::GetFullPath($PSScriptRoot)
if ((Split-Path $backup -Parent) -ne (Split-Path $target -Parent) -or
    -not $payload.StartsWith($work + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $backup -eq $target -or (Test-Path -LiteralPath $backup)) { exit 1 }
$moved = $false
$installed = $false
[IO.File]::WriteAllText((Join-Path $work 'ready'), 'ready')
try {
    $parentProcess = Get-Process -Id $config.pid -ErrorAction SilentlyContinue
    if ($parentProcess -and -not $parentProcess.WaitForExit(120000)) { throw 'Player did not exit' }
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try { [IO.Directory]::Move($target, $backup); $moved = $true; break }
        catch { if ($attempt -eq 59) { throw }; Start-Sleep -Milliseconds 500 }
    }
    [IO.Directory]::Move($payload, $target)
    $installed = $true
    Start-Process -FilePath (Join-Path $target $config.executable) -WorkingDirectory $target -WindowStyle Hidden
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $work 'error.log') -Encoding UTF8
    if ($moved) {
        if ($installed) { [IO.Directory]::Move($target, (Join-Path $work 'failed')) }
        [IO.Directory]::Move($backup, $target)
        Start-Process -FilePath (Join-Path $target $config.executable) -WorkingDirectory $target -WindowStyle Hidden
    }
}
'''


def launch_installer(update: PreparedUpdate):
    if sys.platform != "win32":
        raise RuntimeError("自动安装仅支持 Windows 便携版。")
    config = dict(target=str(update.target), payload=str(update.payload),
                  backup=str(update.target.with_name(update.target.name + ".backup-" + update.work.name)),
                  executable=update.executable, pid=os.getpid())
    (update.work / "install.json").write_text(json.dumps(config), encoding="utf-8")
    script = update.work / "install.ps1"
    script.write_text(INSTALL_SCRIPT, encoding="utf-8-sig")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    process = subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-File", str(script)], cwd=update.work,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 5
    while not (update.work / "ready").is_file():
        if process.poll() is not None or time.monotonic() >= deadline:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            raise RuntimeError("无法启动更新安装器，继续使用当前版本。")
        time.sleep(0.05)
