# Codex MV Kit

把参考审查、真实姿势生图、分层动画、字幕、音画检查和恢复整理成可复用的 **2D sprite MV 工具与 Codex 工作流**。

这是从实际长篇人物替换 MV 制作中抽出的通用实现。歌曲、角色和镜头由创作者定义；工具负责把明确的素材与时间线变成可检查、可恢复的工程。它目前不自动理解整首歌，也不生成连续 3D 动作。

## 已包含

- 可移动的 JSON 工程、场景优先级、RGBA 图层与真实 pose cel 切换。
- 全局时间与共享循环时钟，裁剪预览保持整片相位；统一平移、等比缩放、旋转及完整表面遮挡。
- 视频按需读取、默认 12 帧缓存；明确的循环、越界报错或尾帧 hold。
- 独立 ASS 字幕与字体目录；防止把第二套字幕重复叠到已烧字视频。
- 原生帧索引 / integer PTS 取证、探索性 BPM / phase 分析、完整声明音频窗口比对。
- `ceil(duration × fps)` 整数帧输出、技术 QA、声明依赖的 SHA-256 归档与实际恢复。
- [AGENTS.md](AGENTS.md)、[制作流程](docs/workflow.md)、[生图说明](docs/asset-generation.md)和[任务提示词](prompts/README.md)。

公共仓库只提供代码、文档和运行时生成的原创几何示例。没有附带来源项目的歌曲、歌词、角色图、原作帧、字体、缓存或历史备份。

## 十分钟跑通示例

需要 Python 3.11+、FFmpeg 和 FFprobe。FFmpeg 需要支持 `libx264`、AAC；使用 ASS 时还需 `subtitles` / libass。Python 依赖安装在下面这一步。

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
python -m pip install -e .
codex-mv doctor
codex-mv demo workspace/demo
codex-mv validate workspace/demo/project.json
codex-mv render workspace/demo/project.json --output workspace/demo-full.mp4
codex-mv render workspace/demo/project.json --start 0.75 --duration 2.2 --output workspace/demo-preview.mp4
```

也可使用 `python -m codex_mv` 代替 `codex-mv`。示例自动创建 4 秒原创点击音、三个透明几何 cels、字幕及两个场景；不会下载素材或调用图片 API。输出默认拒绝覆盖，重跑请使用新目录 / 文件名。

外部工具不在 PATH 时，在 PowerShell 设置：

```powershell
$env:MV_FFMPEG = 'D:/tools/ffmpeg/bin/ffmpeg.exe'
$env:MV_FFPROBE = 'D:/tools/ffmpeg/bin/ffprobe.exe'
codex-mv doctor
```

或者用全局参数 `codex-mv --ffmpeg /path/to/ffmpeg --ffprobe /path/to/ffprobe doctor`。参数要放在子命令前。路径只是示例，仓库不依赖具体磁盘。

## 制作自己的 MV

1. 让 Codex 先读 [AGENTS.md](AGENTS.md) 和[工作流](docs/workflow.md)，核对自己有权使用的参考、音频与角色。
2. 从合成 demo 的 `project.json` 起步，替换 `assets`，用相对路径记录素材，并保留来源与生成说明。
3. 按[整身姿势模板](prompts/whole-body-pose.md)准备真实 cels。宿主有内置图片生成工具时可按模板使用；没有时手动导入 PNG。引擎不需要 API key。
4. 在 `scenes / layers / keyframes` 写入已核镜头；共享动作沿用同一 `clock.epoch / period`。动作、屏幕字幕与演唱起点分别核对。
5. 先渲染 2–5 秒预览，实际看姿势、方向、出入场与遮挡，再渲染整片；技术 QA 通过后仍需完整视听检查。
6. 声明全部依赖，打包、在新目录恢复并重建。归档成功与视觉验收分别记录。

```bash
codex-mv extract-frame workspace/demo-full.mp4 --index 37 --output workspace/native.png
codex-mv analyze-audio workspace/demo/assets/clicks.wav --output workspace/rhythm.json
codex-mv qa workspace/demo/project.json workspace/demo-full.mp4
codex-mv pack workspace/demo/project.json --output workspace/demo-inputs.zip
codex-mv verify-archive workspace/demo-inputs.zip
codex-mv restore workspace/demo-inputs.zip workspace/demo-restored
codex-mv render workspace/demo-restored/project.json --output workspace/demo-restored.mp4
```

`pack` 只收集工程声明的资产、音频、ASS、字体目录和 `support_files`，不会盲目压缩整个工作目录。归档可能包含你自己的私有素材，请与公共源码仓库分开管理。

## 给其他 Codex 的入口

可以把下面这段交给新代理，随后填入自己的需求：

> 请先读 AGENTS.md、docs/architecture.md 和 docs/workflow.md，跑通合成 demo。然后根据我提供的已授权参考和角色定义，建立独立工作目录。先做保留全局相位的短对照，实际审查整体姿势、层次和字幕，再决定成片。保留提示词、素材来源、native PTS、参数、未知项与恢复报告；不要把技术通过当作视觉认可。

| 路径 | 内容 |
| --- | --- |
| [docs/index.md](docs/index.md) | 文档导航 |
| [docs/architecture.md](docs/architecture.md) | 实际支持的 JSON 字段、时钟、渲染边界 |
| [docs/migration.md](docs/migration.md) | 从机器路径和多版本脚本迁移到独立组件 |
| [docs/qa.md](docs/qa.md) | 帧数、音频、字幕、原生时间与人工审片 |
| [docs/backup-and-restore.md](docs/backup-and-restore.md) | 输入依赖归档与恢复 |
| [docs/github-release.md](docs/github-release.md) | GitHub 发布准备和上传命令 |
| [prompts/README.md](prompts/README.md) | 角色、整身姿势、表情、背景、封面与来源模板 |
| `src/codex_mv/` | CLI 和通用引擎 |
| `tests/` | 无第三方媒体的回归测试 |
| `scripts/smoke_demo.py` | 实际 CLI 端到端渲染与恢复 |

## 开发与检查

```bash
python -m unittest discover -s tests -v
python scripts/check_release.py
python scripts/smoke_demo.py --workdir workspace/smoke
```

测试素材均在临时目录程序生成。需要 FFmpeg 的测试在工具缺失时会跳过，因此发布前应确认 `doctor` 和端到端 smoke 实际通过。CI 配置覆盖 Windows / Ubuntu、Python 3.11 / 3.12；配置存在不代表远端任务已运行。首次版本的实际结果见[本地验证记录](docs/validation.md)。

`check_release.py` 在 Git 仓库里检查实际跟踪清单，扫描常见密钥、个人目录、私有媒体、归档和大文件。扫描不能代替人工检查素材权利和公开范围。

## 已知边界

当前是显式 2D 图层引擎，不包含自动全片分镜、复杂 rig、连续 3D 转身、音素级口型、完整歌词自动对齐或原作 shader 复现。普通视频层按 CFR 名义帧率采样；源视频 VFR / native PTS 的证据通过 `extract-frame` 独立记录。BPM 和 phase 是估计，可能存在半拍 / 倍速歧义。

渲染报告的 `input_frame_hashes` 对应烧字前 RGB；不能用它证明字幕或有损编码逐像素相同。音频比较覆盖全部声明窗口，静音相关性为 `null`。`visual_acceptance` 默认 `false`，需要创作者实际审片。

## 许可

代码、文档和程序生成示例采用 [MIT](LICENSE)。Python 库、FFmpeg、你导入的音乐、字体、角色图与服务输出各按其授权处理；仓库许可不会重新授权这些素材。仓库不会自动购买服务、安排定时任务或发布到外部平台。
