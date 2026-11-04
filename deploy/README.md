# jfcs62 部署

入口 `http://10.210.32.30:18765/`。当前基线为 UX Pilot `856cc287f99f9a7ee161cdcb0dbb3b47f3e00a19`（2026-11-04 01:15），恢复原版完整工作台并保留本机部署兼容扩展。

- `/`：输入仓库、网站、体验目标、任务数、重复次数和步骤上限。
- `/jobs`：任务进度、取消、事件、报告与分析回放。
- `/stats`：每日任务数、排队/执行/总时长、模型调用和已知/未知用量。
- `/errors`：工具、类别、归一化签名聚类，状态筛选、批量处理及样本回放定位。
- `/deploys.html`：原有增量部署记录；`/deploys` 跳转到同一页面。
- `/reports/...`：今天已有的历史报告与新增回放链接保持可用。

## 状态与进程

独立目录 `/root/zt_dir/ai_base/DuckKing_ux_review`。`releases/<version>` 存代码/venv，`current` 指向当前版本。`workbench/workbench.sqlite3` 是原版 Store 的持久队列、事件、模型调用、错误与处理状态，`workbench/jobs` 保存新任务证据。

`duckking-ux-web.service` 运行原版 FastAPI 工作台及单 worker，由 `ai_ux.jfcs62_web` 提供兼容路由。旧的 `duckking-ux-worker.service` 停止并禁用，避免两套队列同时执行。旧 `state/jobs`、`artifacts/agent`、`artifacts/projects` 和日志保留；历史迁入有幂等检查，原始事件不改写。报告展示文件更新前保存到每个批次的 `.before-workbench-restore`。

`duckking-ux-eval.service` 只用于人工执行旧的 DuckKing 固定任务配置，不随开机启动。不要与网站任务同时手工启动。

API Key 仍使用主机的 `/root/.secrets/zhipu_api_key` 和 `/root/.secrets/minimax_api_key`，不进入发布包。页面 API 不返回密钥。新工作台本机服务需要读取凭据以执行内置 worker。服务写权限限定在 workbench、浏览器缓存及兼容队列目录。

## 构建与发布

```sh
python3 scripts/build-release.py --reference ../DuckKing --reference-ref <DuckKing提交> \
  --change "相对上一成功版本的本次修改"
```

`--change` 可重复，不能复制累计功能清单。上游基线 commit 与本地扩展快照分别记录。包中不含 API Key、历史证据、原始工作台数据库或私有配置。

通过现有 JumpServer SSH 上传包和 `install-jfcs62.sh`。在后台执行安装并保留日志/退出码：

```sh
bash install-jfcs62.sh <archive.tar.gz> <归档SHA256> <version>
```

安装核对归档哈希，用 `uv.lock` 导出且强制校验每个依赖哈希，通过镜像下载。使用主机已有 Python 3.12、Chrome、系统 ffmpeg。Playwright 1.64 不列出 Ubuntu 20.04，安装时选择同一通用 Linux x64 的 Ubuntu 22.04 下载项至独立 `browser-cache`；切换前实际验证录像与 ffprobe，不修改系统 ffmpeg。

安装运行完整测试、分支覆盖率、Ruff 和录像预检后切换 `current`。随后运行：

```sh
current/.venv/bin/python current/deploy/publish-jfcs62.py
```

发布前检查旧 JSON 队列与当前 SQLite 队列；有排队或运行中的任务时停止发布，保持服务和任务状态。脚本停用旧 worker，执行 `ai_ux.restore_history`，安装/检查 systemd unit，启动原版工作台，并检查全部功能入口及 API 后写入成功记录。历史命令行批次作为迁入任务纳入统计，原网页任务保留原 ID；用量/错误从实际事件读取，缺失用量保持未知。首轮缺少录屏组件的错误可从 `run.json` 派生为单独的 `imported-events.jsonl`，带 `derived_from`，不修改原始 events。

历史报告复制到新队列的任务目录以支持原版 API；源码 checkout 不会通过报告端点提供。旧报告 URL 与部署日志保持原处。已有 SQLite 数据库和错误处理状态不被清空。

## 运行检查

```sh
systemctl status --no-pager duckking-ux-web.service
journalctl -u duckking-ux-web.service -n 60 --no-pager
curl --fail http://10.210.32.30:18765/api/health
curl --fail http://10.210.32.30:18765/api/errors
```

页面刷新不会中断任务。服务重启时原版工作台会把进行中的任务标为 interrupted，不自动重复付费调用。升级前先等当前任务结束；回滚代码时保留 workbench 与所有历史证据。
