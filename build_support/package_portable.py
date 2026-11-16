"""Archive the completed PyInstaller folder and verify ZIP integrity."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    dist = Path(__file__).resolve().parents[1] / "dist"
    folder = dist / "EchoPlay - 0.2.2"
    if not (folder / "EchoPlay - 0.2.2.exe").is_file():
        raise SystemExit("Run PyInstaller with EchoPlay.spec first")
    (folder / "使用说明.txt").write_text(
        "EchoPlay 0.2.2 免安装版\n\n"
        "请先完整解压 ZIP，再运行 EchoPlay - 0.2.2.exe。\n"
        "请勿在压缩包内直接运行，也不要单独移动 EXE；"
        "_internal 文件夹必须与 EXE 放在一起。\n"
        "无需安装 Python。曲库和登录数据保存在当前 Windows 用户目录。\n",
        encoding="utf-8-sig",
    )
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    archive = dist / (folder.name + ".zip")
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=6) as output:
        for path in files:
            output.write(path, path.relative_to(dist))
    with ZipFile(archive) as output:
        if bad_file := output.testzip():
            raise RuntimeError(f"ZIP integrity check failed: {bad_file}")
    print(f"ZIP verified: {archive.name}, {archive.stat().st_size} bytes, "
          f"{len(files)} files, {sum(path.stat().st_size for path in files)} bytes unpacked")


if __name__ == "__main__":
    main()
