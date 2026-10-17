# 架构与时间模型

本工具将可审查的项目 JSON 渲染为本地视频。它适合分层 2D 角色、显式 pose cels、循环和关键帧；角色生成、歌曲理解、精细动作与风格判断需要独立工作。不要把合成 demo 的成功等同于任意素材均可自动产出高质量 MV。

## 数据流

```text
获授权源素材 → 来源/时间审查 → project.json → validate
                        ↓                     ↓
                  人工姿势检查          2–5 秒对照片段
                                              ↓
                                      整片渲染与视听 QA
                                              ↓
                                    manifest/ZIP/实际恢复
```

源素材审查产生事实记录；project JSON 保存已决定的镜头参数。把猜测与事实分开，使下一位 Codex 能知道哪些节拍、姿势或字幕仍需确认。

## 当前引擎范围

| 模块 | 职责 | 边界 |
| --- | --- | --- |
| `project` | 解析并校验相对路径、尺寸、时间和层定义 | 不是通用剪辑工程转换器 |
| `timing` / `audio` | 全局节拍换算、估计 BPM/phase、完整窗口音频比较 | 不自动确定演唱、歌词或小节首拍 |
| `media` | FFprobe 信息、逐帧读取、有界缓存 | 请求时间不是 native PTS 的替代品 |
| `render` | 场景选择、sprite cels、统一变换、字幕与音轨输出 | 不生成真实 3D 转身、不保证生成图身份一致 |
| `qa` / `source` | 计数、按 native PTS/索引取帧并保存证据 | 数值通过不能证明视觉通过 |
| `archive` | 有清单的归档、校验与恢复 | 不代替媒体授权审查或视听 QA |

当前图层支持 `normal`、`negative`、`white_outline` 效果；字体目录、视频循环和明确的尾帧 hold 也有配置。分部件 rig、复杂背景 FX、镜头跟踪、逐字歌词和音素口型需要另外设计扩展。先读源码和 CLI `--help`；不要把本页的制作建议当成存在的配置选项。

## 全局时钟

整片 frame `n` 的时间为 `n/fps`；预览从 `start` 开始时，frame `n` 使用 `start+n/fps`。以 `[start, end)` 表示活动窗口，避免边界上两个片段同时抢占。

循环的定义是：

```text
local_phase = (global_time - epoch + shift) % period
beat_time(k) = phase + k * 60 / bpm
```

`epoch` 是动作统一原点，`shift` 是显式相位修正，`period` 是循环秒数。裁剪片段、换 pose、重叠 scene 或移动字幕都不能偷偷改变原点。在歌曲 41 秒处截取 3 秒预览，其时间依然从 41 秒开始；不能把动作又从零拍播放。

BPM 候选可能对应半速或倍速；同一节奏的两种 phase 也可能差半拍。先列出候选与不确定性，再听整拍、看动作锚点并比较几处间隔较远的段落。自动估计不负责判断重拍、演唱起音或歌词语义。

## 独立事件轴

| 事件轴 | 要记录的事实 |
| --- | --- |
| Source/native | 源文件、stream time base、frame index、实际 PTS、相邻帧 |
| Body/pose | 哪个整身 cel 在什么 global time 开始/结束 |
| Vocal | 音频中可听见的起音、延音和结束，容差及依据 |
| Display/caption | 屏幕文字何时出现/消失、是否已经烘焙 |
| FX | 线稿、闪光、色板切换的作用域与起止 |

`first_alpha` 指目标 alpha 第一次非零；`first_clear` 指按预先声明的可读性标准第一次清楚可辨。淡入会让两者不同。源画面在 first-clear 发生的事件、当前渲染层的 active start 和人声 onset 也可能不同。调色变化不足以证明歌词、演唱或动作同时变化。

以上轴是审查模型；当前 JSON 只表达部分渲染事件。复杂制作可用独立 timing 表保存证据，再将确定的结果写入引擎已有字段。参见 [QA](qa.md)。

## 项目 JSON 的核心关系

- 根字段包含 `schema_version`、`duration_seconds`、`fps`、`canvas`、`assets` 和 `scenes`。
- `assets` 用稳定 ID 引用项目内相对文件，并记录来源与许可。路径使用 `/` 分隔，相对于 `project.json` 所在目录；移动整个项目后仍应能校验。禁止绝对路径、`..` 和逃出根目录的链接。
- 活动场景按优先级选择；同优先级重叠属于歧义，应在校验时修正。场景间空档显示声明的 canvas 背景。
- 图层按 `z` 排列，`x/y` 表示中心；使用统一的 `scale`、`rotation`、`opacity`。关键帧的 cel 切换应引用真正不同的资产。
- `clock` 为图层提供同一全局时间下的循环取值。不要把 keyframe 时间、global time 和 source PTS 混用。
- 图层 `effect` 为 `normal`、`negative` 或 `white_outline`；`outline_width` 为 1–64 的整数。效果作用于本图层；改变效果不应重新开始时钟。
- 视频资产可指定 `source_offset_seconds`、`loop`、`end_behavior`。越界默认 `error`；只有明确设置 `end_behavior: "hold"` 才允许尾帧停留，并在报告记录 held frames。不要无声地重复尾帧掩盖源片不足。
- `captions.baked` 表示画面是否已有字幕；视频资产也可声明 `baked`。已有烘焙字幕的画面不能再无差别叠加字幕。`captions.fonts_dir` 是可选的相对字体目录，对应 FFmpeg `fontsdir`。
- 额外说明、timing 表、生成提示词和 provenance 可放入 `support_files` 相对文件清单，供归档收集。仅放到项目目录但未声明的文件不会自动打包。

从 CLI 生成的 demo 开始修改最稳妥。新增字段必须有解析、校验、渲染和文档支持；项目校验成功不能用来证明未知字段已产生预期效果。

没有 `layer.clock` 时，`keyframes[].time` 是项目绝对秒；有 clock 时是周期内秒数。变换属性在关键帧间线性插值，cel 使用当前已到达关键帧的 `asset`，而非两张 cel 混合。视频无 clock 时按 `global_time-layer.start+source_offset_seconds` 取源时间；有 clock 时使用循环 phase 加 offset。视频 reader 的 frame index 用于渲染采样，原生 PTS 证据使用 `source` 工具另行提取。

## 渲染与内存

先确定 `N=ceil(duration*fps)`，输出恰好 `N` 帧；最后一帧时间是 `start+(N-1)/fps`。不要只依靠容器 `duration` 推断帧数，也不要在编码末端额外使用 `-t` 把正确末帧截掉。非整数帧时长可使容器时长略长于请求窗口，应记录差值并明确音频选择窗口。

视频解码采用有界缓存，默认 12 帧。分辨率、帧率与持续时长上升时，不能通过预载整片 RGB 来掩盖随机访问性能问题。分析接触表可以降采样，但交付检查必须回到最终尺寸。

渲染报告的 `input_frame_hashes` 是叠字幕前、送入编码器的合成 RGB 字节哈希，可用于 crop/full 动作等价检查；它们不能证明烧录后的字幕或有损编码像素相同。

遮挡需要完整 alpha 表面。若扩展白线稿模式，只把可见颜色改成线稿，保留身体 alpha 参与前后遮挡；线稿与遮挡必须采用同一位置、尺度、旋转和当前 cel。对单个角色的效果要限制到该角色层；不能意外污染背景、另一个角色或字幕。
