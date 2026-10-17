# 示例工程

运行下面的命令，在忽略目录里生成完整、可运行且可再分发的原创示例：

```bash
codex-mv demo workspace/demo
codex-mv render workspace/demo/project.json --output workspace/demo.mp4
```

示例生成器在 [demo.py](../src/codex_mv/demo.py)，实际 JSON 字段在[架构文档](../docs/architecture.md)。这里不提交带外部歌曲或角色的半成品模板，也不要求先下载任何素材。

修改示例时，保留资产 ID 与项目相对路径，把生成提示词、素材许可、时间表和审查记录列入 `support_files`。工作目录默认被 Git 忽略。只有明确允许公开再分发的资料才进入源码提交。
