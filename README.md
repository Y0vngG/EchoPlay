# EchoPlay

一款支持 B 站音轨播放和本地歌单管理的 Windows 桌面播放器。

[![下载 Windows 便携版 ZIP](https://img.shields.io/badge/Windows-%E4%B8%8B%E8%BD%BD%E4%BE%BF%E6%90%BA%E7%89%88-0078D4?style=for-the-badge)](https://github.com/Y0vngG/EchoPlay/releases/latest/download/EchoPlay-Windows.zip)

完整解压 ZIP 后，双击文件夹中的 EchoPlay EXE 即可运行，无需安装 Python。请将 EXE 和 `_internal` 文件夹放在一起。

## 主要功能

- 无需登录即可搜索 B 站视频，支持分 P 播放，也可粘贴视频链接加入曲库。
- 管理本地曲库和自定义歌单，查看搜索与播放记录。
- 支持在线播放、拖动进度和列表循环、单曲循环、随机播放。
- 支持 B 站扫码登录，登录状态加密保存在本机。
- 提供亮暗主题、Excel 皮肤，以及可自定义的快捷键和任务栏图标。
- 设置页面最下方显示当前软件版本号。
- 当前版本为 0.2.3，版本检查和自动更新入口已注释；升级时请手动下载并解压便携版。

播放品质默认使用 1080P 档，选择最高可用的非 HiRes 音轨并优先使用杜比音轨。360P 档从对应的视频流播放声音。HiRes 档需要有效大会员，视频未提供无损音轨时回退到 1080P 档。

## 从源码运行

在 Windows 上使用 Python 3.13，并在项目目录中运行命令。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

使用 Conda 时，可在独立的 Python 3.13 环境里安装依赖并启动。曲库和登录状态保存在当前 Windows 用户目录，从源码运行时的日志位于 `logs/echoplay.log`。

## 打包 Windows 便携版

打包使用与源码运行相同的 Python 环境。

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm EchoPlay.spec
python -m build_support.package_portable
```

打包完成后，便携版 ZIP 位于 `dist/` 目录。发布新版本时，将 ZIP 以 `EchoPlay-Windows.zip` 的文件名上传到 GitHub Release，下载按钮即可指向最新版本。

### 版本与发布

1. 修改 `echoplay/version.py` 中的 `VERSION`（当前为 `0.2.3`），版本号使用 `主版本.次版本.补丁版本`。
2. 按上述命令重新打包，文件名、EXE 版本信息和包内 `update.json` 会同步生成。
3. 在 `Y0vngG/EchoPlay` 创建对应标签（如 `v0.2.3`）的正式 Release，上传 `dist/EchoPlay-Windows.zip` 和自动生成的 `dist/SHA256SUMS.txt`，并设为 Latest。便携包版本应与标签一致。

`echoplay/app.py` 中更新器的导入、创建及定时启动代码已注释。更新器实现仍保留，但当前程序不会启动版本检查、下载更新或自动替换。修改源码不会改变已经发布的 EXE，需要重新打包才能生效。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 致谢

B 站接口格式解析参考了 [yt-dlp 的 B 站取流实现](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)。
