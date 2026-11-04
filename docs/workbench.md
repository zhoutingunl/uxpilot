# 异步工作台

UX Pilot 的命令行评测是单次运行入口；异步工作台为多个评测提供网页、队列和持久状态。它复用同一套 `discover → evaluate → judge → source_map`，不会复制一套网页 Agent。

```mermaid
flowchart LR
    U[用户输入仓库 + 演示 URL] --> API[FastAPI Workbench]
    API --> Q[SQLite jobs 队列]
    Q --> W[单 Worker 锁 / 异步任务]
    W --> P[Planner + Explorer + Judge]
    P --> A[任务 artifacts / report / replay]
    W --> E[events.jsonl]
    E --> DB[(SQLite events / calls / errors)]
    DB --> S[/stats]
    DB --> C[/errors 工具错误聚类]
    A --> R[/jobs 报告与分析回放]
    D[显式发布记录 API] --> DB
    DB --> V[/deploys]
```

## 任务生命周期

启动：`uv run uxpilot-web`（默认 `127.0.0.1:8088`，可用 `UXPILOT_PORT` 修改）。

1. `POST /api/jobs` 校验公网演示地址、HTTPS 仓库地址和队列上限，创建 `queued` 记录。
2. 单 Worker 通过 SQLite `BEGIN IMMEDIATE` claim 任务并更新 `preparing → planning → evaluating → completed/failed`。
3. 每个 UX Pilot 事件通过 ContextVar sink 写入 SQLite，模型调用拆到 `calls`，错误拆到 `errors`；事件仍保留在任务 artifacts。
4. 浏览器/服务进程重启时，启动恢复会把 `running` 标为 `interrupted`，不冒充成功。取消只取消自己的任务。
5. `report.html`、`analysis-replay.html` 和逐运行 `replay.html` 由任务目录提供，路径检查防止跨任务文件读取。

任务报告中的“分析回放”按时间展示阶段、页面观察、操作意图、动作结果、模型调用状态、错误与截图/视频链接；不保存模型内部推理链，也不把回放链接交给模型自行填写。回放只说明发生了什么，人工仍需确认问题严重度和修改范围。

## 错误聚类

`analytics.classify_error()` 先按来源分类：policy、authentication、rate_limit、upstream、timeout、schema、not_found、network、invalid_input、other；然后对 URL、运行 ID、数值等动态部分归一，形成稳定 `cluster_id`。这避免每次随机 run ID 把同一错误拆成不同问题。重复任务数达到 2，且属于可修复类别时标记为候选；`/errors` 支持筛选和批量设置状态。

错误聚类依赖 UX Pilot 的结构化错误事件，不读取供应商原始响应正文。失败的模型调用缺失 token 时显示 unknown usage，不以 0 充数。聚类是修改排序辅助，不能自动证明根因，也不会自动提交代码。

## 统计口径

- 每日提交数是评测任务提交数，不是 Git commit 数。
- 总耗时、执行耗时和排队耗时分开，均包含/排除关系在 `/stats` 的说明中显示；模型思考时间属于评测运行耗时，不等于产品等待。
- Token 只聚合供应商明确返回的非负整数；缺失用量单独计数，不推算账单。
- 时间范围按 Asia/Shanghai 展示，内部使用 UTC 时间戳。

## 部署记录

工作台不会通过启动进程自动生成成功部署。`POST /api/deploys` 需要版本、40 位 commit、目标、`health/pages/assets/version` 验收 checks；成功记录必须全部通过，coverage（如果提供）必须绑定同一 commit。`/deploys` 可显示 GitHub commit/compare 链接和最近 100 条记录。

生产部署仍由外部 CI/发布系统完成；把工作台暴露到公网前必须配置反向代理认证、持久目录、备份和测试账号。`UXPILOT_ALLOW_LOCAL=1` 只用于受信任的本机测试；公网地址、Git 凭据和页面内容都按不可信输入处理。

## jfcs62 兼容部署与历史迁移

`ai_ux.jfcs62_web` 复用本工作台，提供已有 `/reports/...`、`/api/reviews` 和 `/deploys.html` 的兼容入口。持久历史迁移使用 `ai_ux.restore_history`：保留原始事件和报告备份，批量写入 SQLite，支持中断后继续且不重复计算调用或错误。缺少的旧结构化错误从保存的 `run.json` 派生，带 `derived_from`，不编造模型用量。

发布脚本核验归档与依赖哈希、完整测试和实际录像后再切换版本。部署记录只描述相对上一成功版本的增量改动，并绑定对应快照的覆盖率。详情见 [部署说明](../deploy/README.md)；这些主机适配不会替换通用 `uxpilot-web` 入口。

[Web 界面截图](screenshots/README.md)展示真实页面和合成示例数据，生成脚本不会调用模型或操作公开演示站。
