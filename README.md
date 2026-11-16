# EchoPlay

一款支持 B 站音轨播放和本地歌单管理的 Windows 桌面播放器。

## 主要功能

- 无需登录即可搜索 B 站视频，支持分 P 播放，也可粘贴视频链接加入曲库。
- 管理本地曲库和自定义歌单，查看搜索与播放记录。
- 支持在线播放、拖动进度和列表循环、单曲循环、随机播放。
- 支持 B 站扫码登录，登录状态加密保存在本机。
- 提供亮暗主题、Excel 皮肤，以及可自定义的快捷键和任务栏图标。

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

打包后生成 `dist/EchoPlay - 0.2.2.zip`。完整解压后双击 `EchoPlay - 0.2.2.exe` 即可运行，无需安装 Python。请将 EXE 和 `_internal` 文件夹放在一起。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 致谢

B 站接口格式解析参考了 [yt-dlp 的 B 站取流实现](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)。
