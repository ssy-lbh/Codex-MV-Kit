# 将 Codex MV Kit 发布到 GitHub

本页针对这个工具仓库的首次发布。公共内容是 MIT 许可代码、文档、提示词和生成 synthetic demo 的程序；demo 的几何图形、点击音和视频在使用者本地按需生成，不作为媒体文件提交。使用者自己的歌曲、歌词、角色图、字体、日志、视频和项目 bundle 留在私人项目中，适用各自许可。

先完成本地准备，再发布。用户尚未明确授权首次外部发布时，准备到可审查状态即可；已有明确授权就按该账号、仓库和可见性继续，不必对每个本地命令重复确认。以下 `OWNER`、`REPOSITORY` 必须替换为实际值；它们不是可直接上传的默认账号。

## 1. 在仓库根目录验证本地代码

需要 Python 3.11+、Git 和支持当前编码/字幕功能的 FFmpeg/FFprobe。安装和环境说明见 [README](../README.md)。依次运行，每条成功后再继续：

```text
python -m pip install -e .
python -m codex_mv doctor
python -m unittest discover -s tests -v
python scripts/smoke_demo.py --workdir workspace/release-smoke
```

`workspace/release-smoke` 必须不存在；重复运行换新目录。smoke 实际生成合成输入、整片与裁剪预览、native 取帧、音频分析、归档、恢复和再渲染，检查恢复前后合成 RGB 帧哈希。它不证明生产角色姿势正确，也不构成最终视觉验收。

本地测试通过仅说明本地测试通过。仓库中有 [GitHub Actions 配置](../.github/workflows/ci.yml)，但在 GitHub 对具体提交实际运行并通过前，不能写“远程 CI 已通过”或添加暗示其成功的结论。

## 2. 检查将进入 Git 的实际文件

若当前目录尚未由 Git 管理，先初始化；已有仓库跳过这一条，不要重建它的历史：

```text
git init -b main
```

查看当前状态，暂存本次计划公开的文件，再检查实际 index：

```text
git status --short
git add .
git ls-files
git diff --cached --stat
git diff --cached
python scripts/check_release.py
```

`check_release.py` 在 Git 仓库内检查 `git ls-files` 的实际跟踪清单，而不是只读 `.gitignore`。它会检查必需入口、媒体/字体/压缩包、私有或生成目录、可疑凭据、个人绝对路径、大文件和意外二进制文件；在 Git index 为空时会失败。尚未初始化 Git 时它只能检查候选文件树，不能据此声称已经检查 Git 跟踪面。

查看脚本报告之外，还要人工读 staged diff 和历史。这个启发式扫描不证明所有隐私或媒体权利都已处理；它读取的是跟踪路径的当前工作文件，不能替代 staged 内容和历史审查。`.gitignore` 对已经跟踪的文件不生效；发现不该公开的文件先调整 index/目录，再重新运行检查，不要通过放宽检查规则隐藏它。

本仓库应提交 `src/`、`tests/`、`scripts/`、`docs/`、`prompts/`、`.github/` 和根目录文本配置；`workspace/`、用户媒体、渲染结果与 ZIP 不进入 Git。没有原作帧或真实歌曲也能运行 synthetic demo。

确认清单后创建本地提交：

```text
git commit -m "Prepare Codex MV Kit for public use"
git status --short
git branch --show-current
```

首次初始化使用 `main`。已有工程若在其他分支，应先确定计划发布的分支；不要为了照抄下方命令擅自改动分支历史。发布后新增本地修复时，重新暂存、检查并提交对应变更。

## 3. 在 GitHub 创建空仓库

以下动作开始接触外部服务。缺少首次发布授权时，此时向用户展示实际账号、仓库名、可见性、拟发布文件范围和本地检查结果，取得明确发布授权再继续。授权已经覆盖这些内容时直接执行。

可以在 [GitHub 新建仓库页面](https://github.com/new) 选择 `OWNER`、填写 `REPOSITORY`、选择约定的可见性。将其创建为空仓库，不预先添加 README、LICENSE、`.gitignore` 或模板内容，因为本地已有这些文件。GitHub 官方的[本地代码导入说明](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)推荐这种空仓库方式，避免不必要的历史冲突。

如果已安装并配置 GitHub CLI，也可以用下面的可选路径创建公开空仓库：

```text
gh auth status
gh repo create OWNER/REPOSITORY --public --description "Portable tools and prompts for agent-assisted sprite music videos"
```

若约定的是私有仓库，将 `--public` 替换为 `--private`。使用已配置的认证方式；不要把 token 写进命令、URL、文档或 Git。`gh` 不是本工具运行所需依赖，不应假设所有 Codex 环境都已安装或登录。创建参数见 [GitHub CLI 官方手册](https://cli.github.com/manual/gh_repo_create)。

## 4. 连接 remote 并首次上传

先检查是否已有 remote。没有 `origin` 时，按空仓库页面的实际地址添加：

```text
git remote -v
git remote add origin https://github.com/OWNER/REPOSITORY.git
git remote -v
git push -u origin main
```

已有 `origin` 就核对它，不重复添加，也不擅自替换为新目标。上传前确认 local `main` 是要公开的已检查提交。推送若因远端已有提交而失败，先查看差异；不要默认 force-push。GitHub 官方[命令行导入步骤](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)说明 remote 和首次 push 的用法。

上传后打开实际仓库，检查 README、许可、文档链接和文件范围，再查看该提交的 Actions 结果。只有实际 jobs 全部通过，才能将该次远程 CI 标为通过；本地 smoke 与远程 CI 分开报告。可以在新的本地目录 clone 已公开仓库并重复安装、测试和 smoke，验证读者得到的内容可用。

## 5. 区分两种 ZIP

| 文件 | 内容与用途 | 是否适合公开 |
| --- | --- | --- |
| Source archive | 指定 Git 提交中的代码、文档与配置 | 提交内容已经审查后可公开 |
| User project input bundle | `project.json` 声明的音乐、角色、字幕、字体和 support files，带 manifest/哈希 | 逐项按用户媒体许可判断，通常保持私有 |

只要本地已有经过检查的提交，可在选定的新输出文件名创建 source ZIP：

```text
git archive --format=zip --prefix=codex-mv-kit/ --output=codex-mv-kit-source.zip HEAD
```

它来自 `HEAD`，不包含未提交的修改、忽略的 workspace 或 Git 历史；先确认提交里已经包含全部发布修复。`git archive` 的具体行为见 [Git 官方文档](https://git-scm.com/docs/git-archive)。此 ZIP 没有 MV project bundle 的 manifest，不能交给 `verify-archive` 或 `restore`；解压后按 README 安装。

用户工程 bundle 使用 `python -m codex_mv pack`，按 [备份与恢复](backup-and-restore.md) 校验并在新目录实际恢复。不要把含音乐或字体的 bundle 作为代码 release 附件自动上传。归档完整性、恢复可用性、人工视听和媒体公开权利分别记录。
