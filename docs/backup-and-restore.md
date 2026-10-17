# 备份、校验与恢复

备份应回答“在另一目录是否能恢复工程并继续工作”。成片是否好看和同步正确需要独立视听 QA；ZIP 完整性不能代替它。

## 需要保留的内容

- `project.json`、实际引用的素材、字幕、允许存档的字体。
- 来源与许可、生成提示词和 provenance、native timing 证据、人工 QA 状态。
- 工具/依赖版本、关键渲染参数、源码版本或源码快照。
- 按交付约定保留的最终视频和关键对照片段。

`pack` 默认收集 project JSON、声明的资产、音频、字幕和字体目录，另外收集 `support_files` 列出的文件。说明、provenance、timing 表等要逐个加入 `support_files`；放进目录不代表自动包含。渲染成片默认不在输入 bundle 中，若交付需要保留，可另行归档，或明确列为 support file。不要把整个工作目录无差别打包。

缓存通常可再生成，可以不打包，但应先证明恢复不依赖它。外部链接不能代替本地必要资产。含有用户媒体的归档保持私有，公开代码的 MIT 许可不会让这些媒体自动可公开。

## 清单与 ZIP

使用 CLI 的 `pack`、`verify-archive`、`restore`，具体参数以 `python -m codex_mv <command> --help` 为准。选择新文件名和尚不存在的恢复目录；不要预先创建恢复目录，避免覆盖工作工程。

完成 [工作流](workflow.md) 的 demo 后，可执行：

```text
python -m codex_mv pack workspace/first-demo/project.json --output workspace/first-demo-inputs.zip
python -m codex_mv verify-archive workspace/first-demo-inputs.zip
python -m codex_mv restore workspace/first-demo-inputs.zip workspace/first-demo-restored
python -m codex_mv validate workspace/first-demo-restored/project.json
python -m codex_mv render workspace/first-demo-restored/project.json --output workspace/first-demo-restored/renders/preview.mp4 --start 1 --duration 2
```

demo 声明的 provenance 会进入 bundle；后来生成的 notes/QA 报告默认不会进入，除非先将它们加入 `support_files`。`pack` 报告包含最终 ZIP 的 `archive_sha256`，需要留作证据时将完整报告保存到归档外。

manifest 至少说明归档格式版本、相对路径、每个文件字节数和 SHA-256；另保存最终 ZIP 自身的 SHA-256。将 manifest 放在归档内，ZIP 哈希放归档外，避免要求文件包含自己的哈希。不要把“有哈希”写成“有可信签名”：同处一个可修改位置的哈希只能检查一致性，不能单独证明来源真实性。

归档前检查实际包含文件，不要把权限未知素材、私人日志、密钥、机器路径和不需要的长视频顺手打包。manifest 本身同样不得暴露私人绝对路径。

## 实际恢复演练

1. 校验 ZIP 和内部 manifest。发现缺失、额外内容或哈希不符应明确报错。
2. 指定尚不存在的新目录执行恢复，工具会创建它；避免覆盖原工程。恢复逻辑应拒绝绝对路径、`..` 路径穿越、越界链接等不安全成员。
3. 对恢复出来的文件重新计算哈希，与 manifest 比较。确认项目引用都在恢复范围内。
4. 在恢复目录运行 validate，再渲染代表性 2–5 秒片段，检查素材和字体是否实际可用。
5. 对比恢复前后的项目和帧证据，记录是否达成渲染可复现；重新审听/审片的状态单独填写。

建议将结果分成三行：`archive_integrity`、`restore_smoke_test`、`visual_audio_review`。例如前两项通过、最后一项待审完全合理；不能为了交付“全绿”而将待审改写为通过。

## 多版本与恢复边界

用版本号或内容哈希关联 project、输入和输出。覆盖一张 PNG 可能让旧 project 使用不同字节；稳定文件名不等于稳定版本。每次可交付版本至少保留输入哈希及参数。

恢复演练不会自动验证媒体授权、清除旧缓存或删除原文件。清理和外部上传是独立动作，按用户授权执行。对外发布 ZIP 前，还需按 [贡献与发布](contributing.md) 检查授权与文件范围。
