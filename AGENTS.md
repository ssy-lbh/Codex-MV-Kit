# Codex MV Kit — agent instructions

本仓库是通用的 2D sprite MV 起步工具。先读 `docs/architecture.md` 与 `docs/workflow.md`；素材、检查、迁移分别见 `docs/asset-generation.md`、`docs/qa.md`、`docs/migration.md`。不要承诺一次提示就完成任意歌曲的专业 MV。源码审查、姿势审片和最终视听检查仍需实际执行。

## Public code and private projects

- 公共示例只使用程序生成或明确可再分发的素材。示例角色用 AngelA、DemonB 等通用身份。不得引入来源项目的帧、音乐、歌词、角色图、字体、日志、版本链或个人机器路径。
- MIT 许可覆盖本仓库代码；外部音乐、字体、插画、生成服务输出依各自授权处理。给每个素材保留来源、许可、修改记录和生成 provenance。许可未知就标明未知，不要自行写成 CC0。
- 用户素材、生成输出、缓存和导出物放在忽略目录或仓库外项目；提交前检查实际 Git 文件清单，不能只依赖 `.gitignore`。

## Implementation invariants

- Python package: `codex_mv`。Python 3.11+，NumPy、Pillow、OpenCV；FFmpeg/FFprobe 是外部可配置程序。对子进程传参数列表，不拼接 shell 命令。路径随项目移动，不写死主机路径。
- 唯一全局时钟：整片 frame `n` 的时间为 `n/fps`，裁剪 frame `n` 的时间为 `start+n/fps`。循环相位为 `(global_time - epoch + shift) % period`。裁剪预览不能重新开始节拍或动作 epoch。
- BPM 估计有半拍/双拍和相位歧义。保留估计方法、不确定性及人工锚点；不能把冲击峰值直接说成小节首拍、演唱起音或字幕入点。
- 动作、演唱、显示字幕、画面 FX 是独立事件轴。native first-clear、first-alpha、当前渲染激活点分别记录。分析源视频时记录实际采样 PTS，不能只写请求时间。
- 视频使用有界缓存，默认最多 12 帧；不把整片 RGB 帧一次装入内存。姿势使用真实整身 cels。水平翻转正面图不能充当实体转身；不能把四肢拼贴变形包装成新姿势。
- 角色使用统一平移、等比缩放、旋转；可见图层与遮挡 alpha 采用相同变换。白线稿效果仍保留完整身体 alpha 遮挡，线稿效果必须限定角色/场景作用域。没有实现的效果应明确列为扩展。
- 帧数使用 `ceil(duration*fps)`。编码时明确整数帧数，防止输出侧 `-t` 截掉末帧。音频比较必须覆盖全部声明窗口；不通过 `min(len(a), len(b))` 隐藏尾部差异。静音相关性为 `None`。
- 字幕使用专用 ASCII 相对 ASS 文件和可用时的 `fontsdir`；验证具体字体和 libass 支持。已烘焙字幕的视频不能再全局叠加第二套字幕。

## Working and verification

- 普通本地分析、可逆编辑和预览按用户任务继续完成；不要增加每一步都确认的流程。渲染到新文件名，保持来源可恢复。现有输出如需替换，遵从用户已有授权；默认拒绝静默覆盖。
- 先做 2–5 秒对照片段，再渲染长片。选择包含起音、pose 切换、重叠遮挡或字幕边界的片段，保留与全片一致的 global clock。
- 改渲染/时间/音频逻辑时运行 `python -m unittest discover -s tests -v`，以及 FFmpeg 可用时的合成端到端 demo。纯文字改动检查链接和命令。不要为通过检查放宽阈值。
- 检查必须区分：schema 合法、帧/样本计数正确、文件哈希完整、恢复成功、人工视听通过。任何一项不能冒充另一项。低分辨率接触表也不能代替最终分辨率审片。
- 归档生成 manifest、ZIP 与哈希，并在新目录执行实际恢复。归档成功不代表成片视觉正确。不得隐式删除原素材或清缓存。
- 多代理按文件分工；接口变更先与整合者协调。所有新功能同步更新文档，不能把建议中的 schema 字段写成已支持接口。

## External actions

内置图片生成工具若宿主支持，可按用户任务使用；工具缺失时保存提示词与输入清单，说明待人工导入。不要假设所有 Codex 环境都有同一种工具。不要自动要求 API key、调用付费 API、上传私有参考图、发消息、安排任务或发布网站/GitHub 仓库。用户已明确授权的范围内继续执行；首次外部发布须有明确发布授权。GitHub 的本地准备工作无需等待发布批准。

`AGENTS.md` 的发现和层级覆盖遵循当前宿主；Codex 官方说明见 [Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。不要修改用户全局配置来强制加载本项目规则。
