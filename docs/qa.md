# 质量检查：证据与视听分开记录

数值检查回答“输出是否符合声明的结构与计数”；人工审片回答“动作、表情、同步和构图是否成立”。两者都要记录，不能把自动通过写成最终视觉批准。

## 检查表

| 层级 | 检查内容 | 通过也不能证明 |
| --- | --- | --- |
| Project | 相对路径、资产存在、时间有效、优先级无歧义 | 素材好看、许可已获批准 |
| Timing | 原点一致、边界帧、BPM/phase 候选 | 真正演唱起音、歌词语义 |
| Video | 整数帧数、尺寸、帧率、首末帧 | 角色身份、自然姿势、无遮挡错误 |
| Audio | 完整声明窗口、样本数、相关性/lag | 听感正常或全部混音意图正确 |
| Archive | manifest、ZIP 哈希、实际恢复 | 成片视听通过、授权完整 |
| Human review | 原尺寸、正常速度、全片播放 | 不应替代可执行数值检查 |

建议在 `notes/qa.md` 为每项记录 `pass / fail / pending / not_applicable`、证据路径、方法和限制。失败保持可见，不能降低阈值或跳过尾部使报告全绿。

## Native PTS 与 first-clear

源视频可能有变帧率、重复帧或不从零开始的 PTS。`requested_time` 只是取帧请求，真实解码出的 `best_effort_timestamp_time` / PTS 才说明采样落在哪里。记录 stream time base、native index、实际 PTS 和“首个不早于 / 最近 / 首个不晚于”的选帧策略。仅使用 `index/fps` 对 VFR 源标注 native PTS 会制造证据。

可用独立 ffprobe 命令导出帧表，文件名仅为你自己项目内的示例：

```text
ffprobe -v error -select_streams v:0 -show_frames -show_entries frame=best_effort_timestamp_time,pts_time,pkt_duration_time -of json source.mp4
```

当前 CLI 的 `extract-frame` 使用 native 帧表；`--time` 选取最近 native PTS，`--index` 选取原生帧序号，并保存 `.evidence.json`，包含请求值、实际 native index/PTS/time、time base、源文件与 PNG 哈希。它仍不判断画面是否已经清楚可读。接入其他取帧工具时，检查其报告是否只有 CFR 估计；不能将估计改称精确 PTS。

检查事件时同时保存：前一个 native frame、候选 frame、后一个 native frame；在必要时扩大范围。预先写明阈值，例如“alpha 任意非零”或“文字达到完整可读状态”。不能在看完结果后改阈值只为让两个事件对齐。

只有拿到真实 alpha 层时，才能把判定称为 first-alpha。若源文件只剩 RGB 烘焙视频，像素亮度、背景差分或色键只能提供代理证据，应标明方法，不能倒推出原始 alpha。

| 名称 | 定义示例 | 与其他轴的关系 |
| --- | --- | --- |
| first-alpha | 目标 alpha 首次非零 | 淡入噪点可能早于可读时刻 |
| first-clear | 在声明标准下首次清楚可辨 | 不自动等于演唱起音 |
| vocal onset | 音频中可听见起音 | 应有听辨/波形证据和容差 |
| display start | 本次渲染何时激活文字层 | 可以是设计选择，与源时间不同 |
| FX start | 线稿、闪光或色板切换 | 不应从调色直接推断歌词开始 |

## 节拍与裁剪一致性

BPM 与 phase 应声明估计方法和误差，特别检查 `bpm/2`、`bpm*2` 以及半拍 phase。STFT hop 或 onset 曲线的时间分辨率限制了精度；不能把粗糙分析写成毫秒级事实。

`analyze-audio` 面向第一音频 stream 的单声道分析，不是人声分离或自动扒谱。报告中的 onset、候选分数和扫描间隔不等于真实节拍精度；还要看有限片长限制与不确定性。无明显节奏的音频应保留低置信度或未知状态。

同一 global time 的动作必须一致。选择一个非零 `start`，分别渲染整片对应窗口和独立预览，检查身体 cel、相位、位置、FX 与字幕。若预览每次开头重新跳起，先查是否把 crop start 写成新的 `epoch`。镜头切换不能隐式重启动作时钟。

## 输出帧与末帧

预期帧数为 `ceil(requested_duration*fps)`。核对实际解码帧数、第一帧与最后一帧；仅检查容器 `duration` 不够。最后一帧采样时间应为 `start+(N-1)/fps`。边界测试覆盖 pose、caption、scene 和 FX 起止的前后帧。

已生成正确整数帧流时，不要再用输出侧 `-t requested_duration` 无意截掉末帧。音频窗口与容器帧量化可能不同，分别报告真实长度；不要把延长音频或丢弃画面尾帧当作默认修复。

## 音频全窗口

比较原音轨与输出的同一声明窗口，使用同一采样率、通道策略、起点与长度。分别记录 reference / candidate 的样本数、时长、lag、相关性和限制；涉及编解码器 padding 时额外报告原始解码长度。

预览输出通常从 PTS 零开始，而原音轨取样从项目 start 加 `audio.offset_seconds` 开始，二者不能传同一个起点。合成 demo 的 1–3 秒预览可这样比较：

```text
python -m codex_mv compare-audio workspace/first-demo/assets/clicks.wav workspace/first-demo/renders/preview.mp4 --reference-start 1 --candidate-start 0 --duration 2 --output workspace/first-demo/notes/audio-compare.json
```

相关性是未对齐补偿的完整窗口比较；lag 是独立诊断，正值表示 candidate 延后。不要根据估计 lag 自动移位后再宣称原输出同步正确。查看报告的 lag 时间分辨率、静音状态和样本计数；CLI `compare-audio` 的成功退出不单独保证相关性达到你需要的阈值，`qa` 会按阈值检查。

禁止先取 `min(len(reference), len(candidate))` 再宣布全轨一致，这会隐藏尾部丢失。样本数不符应明确失败；如果分析需要共同区间，必须把它标为局部诊断并保留完整窗口失败。静音、常量信号等零方差数据的 correlation 是 `None`，不是 `1` 或“完全匹配”。

高相关性不能替代监听。试听开头、结尾、强瞬态与关键切换，并完整播放最终交付文件；注意额外静音、截断、声道变化和偏移。BPM/phase 分析不能从混音中推断具体歌词或演唱者。

## 字幕和字体

- 区分 soft subtitles、独立 ASS 和已烘焙字幕。查看源画面，不能仅看有没有字幕 stream。
- 给 FFmpeg/libass 使用专用临时渲染目录，ASS 文件名采用 ASCII 相对名，如 `captions.ass`，避免平台路径转义问题。以程序参数列表调用 FFmpeg。
- 提供字体时使用 `fontsdir`，检查 libass 是否加载实际字体；仅设置 family 名不能证明未发生 fallback。字体文件同样需要许可记录。
- 项目字段是 `captions.fonts_dir`，值为项目内相对目录。renderer 将它复制到临时 ASCII 目录并传给 libass；仍应实际检查字体是否加载和文本是否可读。
- 若源画面已有烘焙字幕，禁用重复叠加。字幕属于单个预合成素材时，不要把它误当全局字幕需求。
- 检查文字内容、换行、边距、透明度、safe area 和入出点。公共测试使用原创短句或占位符，不嵌入商业歌词。

## 最终视觉审片

逐 pose 检查整身、手指、腕部连接、肩胯透视、脚底基线与身份。实际动作观察跳帧、切换抖动和镜像误作转身。角色重叠时检查所有前后关系；白线稿仍需完整 alpha 遮挡，不能让后方身体从前方躯干内部透出。

用最终分辨率检查透明边缘和线条，再正常速度完整播放。对照短片保留相同时间范围和镜头，避免只挑更有利的一帧。若审片尚未完成，在交付报告里写 `visual_review: pending` 并列出未检事项。归档和所有自动测试通过都不能替你将它改成 `pass`。

## Windows 与 FFmpeg 版本兼容

单张 native 帧按索引选择后，只输出一张 image2 图片，不依赖旧 `-vsync` 或较新 `-fps_mode` 参数。FFprobe / FFmpeg 出错时保留 stderr，不能只报告退出码。

Windows 的包管理器可能通过 shim 启动真正的 FFmpeg。渲染异常时先关闭输入并有界等待它退出；必要时停止本次创建的进程树，再清理临时文件。只结束 shim 可能留下子进程占用日志，掩盖原始素材错误。不能通过忽略清理错误或跳过失败测试解决这个问题。

CLI 的 JSON 与错误信息统一使用 UTF-8，重定向输出和旧 Windows 代码页也应保留中文路径。CI 独立执行四个系统/Python组合，并显示实际工具及依赖版本；一个失败不取消其他组合，所有检查仍需通过。
