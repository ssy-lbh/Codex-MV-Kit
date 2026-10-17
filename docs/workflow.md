# 从歌曲到可交付项目

每次从一个明确 brief 开始：画幅、时长、帧率、音乐文件、期望角色、镜头密度、字幕方式、是否允许生成新素材，以及交付的是视频、源工程还是两者。先完成当地可逆工作；发布或上传前确认任务中已有明确授权。

## 1. 建立可移动工作目录

安装方式和可执行命令见 [README](../README.md)。运行 `python -m codex_mv --help` 查看当前入口，再用 demo 生成可工作的最小项目。真实制作建议在忽略的工作目录或仓库之外。

安装后可先运行这一组本地命令。`workspace/first-demo` 必须不存在；重复试验使用新的目录/输出名。`workspace/` 在本仓库默认忽略：

```text
python -m codex_mv doctor
python -m codex_mv demo workspace/first-demo
python -m codex_mv validate workspace/first-demo/project.json
python -m codex_mv analyze-audio workspace/first-demo/assets/clicks.wav --output workspace/first-demo/notes/audio.json
python -m codex_mv render workspace/first-demo/project.json --output workspace/first-demo/renders/preview.mp4 --start 1 --duration 2
python -m codex_mv qa workspace/first-demo/project.json workspace/first-demo/renders/preview.mp4 --start 1 --duration 2 --output workspace/first-demo/notes/qa.json
python -m codex_mv extract-frame workspace/first-demo/renders/preview.mp4 --output workspace/first-demo/notes/frame.png --time 0.5
```

demo 的圆形/菱形标记和点击音用于检查工程链路，不是角色设计或成片质量样板。`render` 会保存 `.render.json` 报告并运行技术 QA；人工视听状态仍是未评估。ASS 烧录需要 FFmpeg 的 libass 支持；只测试无字幕链路时可给 `render` 加 `--no-captions`，字幕检查随后单独完成。

真实制作的目录可以这样组织：

```text
my-mv/
  project.json
  assets/
    audio/
    characters/angela/
    characters/demonb/
    backgrounds/
  captions/
  notes/
    brief.md
    timing.csv
    source-audit.md
    qa.md
  provenance/
  renders/
  backups/
```

这些子目录是制作约定，不是校验器要求。项目引用一律相对路径。音频、字体、角色图分别记下来源、许可与公开范围；不要把用户素材移动进公共示例以方便测试。

## 2. 审查来源与时间

先 probe 原始音频/视频并保留结果；检查持续时长、stream time base、帧率、是否 VFR、音轨偏移与烘焙字幕。源视频取帧要保存实际采样 PTS 与选择策略。推荐表头：

```csv
event_id,axis,requested_s,actual_pts_s,frame_index,criterion,confidence,notes
anchor-01,source,1.000,,,first_clear,pending,inspect adjacent native frames
```

`pending` 空项要在审查后填写。不能用“请求了 1 秒”证明已经拿到 PTS 恰好为 1 秒的原生帧。first-alpha、first-clear、vocal onset、caption entry 分成不同事件行。详见 [QA](qa.md)。

分析音频得到 BPM 和 phase 候选后，听几处远距离片段并检查半拍/双拍。只把有依据的锚点定稿；不确定性写进 notes。所有角色共享一个 global beat clock。

## 3. 先固定角色，再扩展资产

从 [角色 bible](../prompts/character-bible.md) 生成或整理身份描述、轮廓、颜色、服装与比例，再创建一张正面基准图。角色可用 AngelA、DemonB 这样的占位名；使用者可以替换为自己的原创角色。

对每个姿势提出真实整身要求：肩胯、四肢和衣物随透视一起变化，手完整且紧凑，画布留足边距。确认 cel 后再制作表情与多姿势组。水平翻转只能表示允许的镜像设计，不能充当 3D 转身。不能把生成的手臂接到旧身上后声称完成自然全身姿势。

使用 [asset-generation.md](asset-generation.md) 保存本地文件和 provenance。宿主提供内置图片生成工具时优先按任务使用；没有工具时输出完整提示词和导入清单，继续可完成的工程工作。不要自动要求 API key 或启用付费 API。

## 4. 写镜头与独立事件表

Storyboard 写清镜头窗口、角色位置、pose、遮挡关系、背景和动作循环；另记演唱、显示字幕与 FX 的时间。随后把已支持的字段写入 `project.json`。先 `validate`，不要添加看似合理但未实现的 key。

表情编辑和姿势生成具有随机性；即使提示“其他区域完全一致”，也不能声称非目标区域逐像素不变。做 alpha 边缘和图像差分，检查眼睛、衣领、头发与身形。真的需要像素固定时，在本地把审核过的编辑区域按 mask 合成，并记录处理方式。

## 5. 做 2–5 秒对照片段

至少选一个有切换边界的片段：姿势切换、角色重叠、字幕入点、人声起音或效果变化。用全局 `start` 裁剪，保留原 epoch。片段过短时也要覆盖边界前后的相邻帧。

先对比当前版和候选版，再检查“直接裁剪整片的帧”与“按同一 start 单独渲染的帧”。后者应在编码前图像层面一致；有损编码导致的微小差异需要与时间偏移区分。只有片段判断成立，才扩展到整片。

审片要同时看原尺寸静帧和正常速度播放：脚是否被裁切、手是否多指或断开、身形是否漂移、遮挡是否穿透、线稿是否泄漏到别的角色、字幕是否重复。允许制作低分辨率 overview 便于导航，但不能把它当最终批准。

## 6. 整片、数值检查与人工视听

渲染到新的输出名。保留 project JSON、源码版本、工具版本、输入哈希、起点与请求时长、实际输出帧数、音频比较结果和人工 QA 结论。

数值检查包括 `ceil(duration*fps)` 帧、完整声明窗口样本数、首末帧、各关键边界前后帧，以及从开头到末尾的音频比较。不能先截到两个音频长度的较小值再宣布一致；静音的 correlation 应为 `None`。详见 [qa.md](qa.md)。

人工完整播放交付视频，记录尚存问题。若只完成了自动检查，就如实写“自动检查通过，视觉/听觉待审”；不要输出“完美还原”。

## 7. 归档和交付

按 [backup-and-restore.md](backup-and-restore.md) 创建带 manifest 和哈希的 ZIP，在新的空目录真实恢复并重新 validate、渲染短片。交付视频、工程和原素材可以有不同授权范围，分别列明。

项目要公开到 GitHub 时，先完成本地扫描、测试和可审查的提交清单，最后在已有明确发布授权下执行上传。发布准备见 [contributing.md](contributing.md)。
