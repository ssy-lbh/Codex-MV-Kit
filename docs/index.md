# 文档入口

Codex MV Kit 将 sprite MV 的可重复部分整理为本地工具和工作约定：读源素材、校准时间、准备角色 cels、短片比较、整数帧渲染、检查与恢复。它当前是通用 sprite 引擎的起点，需要创作者定义镜头和复核素材，不能保证一句提示就得到任意风格的成片。

第一次使用按 [工作流](workflow.md) 操作；理解字段和边界看 [架构](architecture.md)。

| 任务 | 文档 |
| --- | --- |
| 理解全局时钟、scene、layer 和当前能力 | [architecture.md](architecture.md) |
| 从新歌到短片、整片和交付 | [workflow.md](workflow.md) |
| 角色设定、真实姿势、表情、背景、来源记录 | [asset-generation.md](asset-generation.md) |
| native PTS、音频、字幕、视觉和帧数检查 | [qa.md](qa.md) |
| 从已有 MV 脚本或版本链迁移 | [migration.md](migration.md) |
| manifest、ZIP、哈希和实际恢复演练 | [backup-and-restore.md](backup-and-restore.md) |
| 贡献、隐私检查与 GitHub 发布准备 | [contributing.md](contributing.md) |
| 首次版本实际本地验证与限制 | [validation.md](validation.md) |
| 本仓库的具体本地检查、空仓库创建和首次 push | [github-release.md](github-release.md) |

[prompts/](../prompts/README.md) 是供 Codex 或其他代理使用的任务模板。方括号是待填写信息；提示词里的 storyboard、native timing 表与 provenance 记录是创作资料，不会自动变成渲染引擎支持的字段。以项目校验器和实际 CLI `--help` 为准。

公共仓库不携带真实歌曲、歌词、原作帧或商业字体。你可以在自己的项目目录使用获授权素材；先核实它们是否允许发布视频、公开源工程或再分发原文件，这三种权限可能不同。仓库 MIT 许可不会替外部媒体重新授权。
