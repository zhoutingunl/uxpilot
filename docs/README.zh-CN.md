# UX Pilot 使用说明

提供**代码仓库地址（或本地目录）+ 线上演示地址**，即可自动规划体验目标、操作网页、保存截图/视频、独立评审并生成修改候选。默认模型是智谱 GLM-5.3 Flash，备用 MiniMax-M3.1 Flash。

[![UX Pilot 分层架构图](diagrams/architecture.png)](architecture.md)

[16:9 横版 PNG](diagrams/architecture.png) · [SVG 矢量版](diagrams/architecture.svg) · [详细长图](diagrams/architecture-detail.png) · [原设计实现状态](design-implementation-status.md)

```bash
uv sync --frozen
uv run uxpilot review \
  --repo https://github.com/markedjs/marked.git \
  --url https://marked.js.org/demo/ \
  --tasks 3
```

默认使用本机 Chrome。也可安装 Playwright Chromium，再把生成配置的 `browser.channel` 改为 `null`，或设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE`。另外需要 ffmpeg/ffprobe。

密钥读取环境变量 `ZHIPU_API_KEY`、`MINIMAX_CN_API_KEY`，或仓库外的 `~/.secrets/zhipu_api_key`、`~/.secrets/minimax_api_key`；不写入报告。运行会消耗模型额度。

## 异步工作台

```bash
uv run uxpilot-web
```

打开 `http://127.0.0.1:8088/`，输入仓库地址和演示地址后后台异步运行。任务、阶段事件和报告保存到 `UXPILOT_DATA_DIR` 的 SQLite；进程重启会把运行中的任务标记为中断并保留证据。

`/jobs` 查看实时进度和分析回放，`/stats` 查看每日提交数、排队/执行/总耗时分布、模型调用和 token 用量，`/errors` 按工具、类别和归一化签名聚类错误并批量标记处理状态，`/deploys` 查看明确记录的发布版本、提交、验收检查和覆盖率。开发启动显示为 development，不会冒充成功发布。

生产使用前需在反向代理层增加认证，配置持久数据目录和测试账号；公网模式只允许公共目标和只读资源，不是完整网络沙箱。

## 常用方式

```bash
# 指定本地源码和关注目标
uv run uxpilot review --repo /path/to/project --url https://demo.example.com/ \
  --goal '找到商品，搜索并查看详情'

# 只生成任务与配置，之后可补充严格业务断言
uv run uxpilot review --repo https://github.com/org/repo.git \
  --url https://demo.example.com/app/ --plan-only

# 复用生成配置，重复三次
uv run uxpilot --config artifacts/projects/<id>/project.yaml eval --repeats 3

# 模型故障后只重做评审，不重新操作网页
uv run uxpilot --config artifacts/projects/<id>/project.yaml \
  judge artifacts/projects/<id>/runs/<batch>/<run>
```

远程仓库检出到本次目录，本地仓库只读；不会安装依赖或启动目标源码。默认通用模式不使用 DuckKing 的房间、模型、语音等选择器。DuckKing 专用配置仍可单独运行。

## 如何解读结果

报告保存 `report.html`、`report.md`、`report.json`，包含实际观察、推断、建议、证据、复现步骤和回归标准。源码定位在体验结束后进行，并验证文件、行号和引用原文；这只说明位置相关，不代表根因或部署版本已经确认。

自动任务有页面加载、JS 错误和横向溢出的基础检查。**没有业务断言时，业务独立验收显示 `not_verified`；不会因为网页能打开就宣布任务成功。** Judge 对业务是否完成的判断单独展示。可配置 `text`、`element_visible`、`url_contains` 等断言，或加入专用适配器。

公开资源的 GET/HEAD 默认允许，用于正常加载 CDN；导航仍限制在配置域名内。额外导航域名用 `--allow-host`，资源域名用 `--resource-host`。登录、验证码、第三方支付和复杂富文本操作不是保证开箱即用的范围，必要时记录受限并补充测试配置。

原始录屏保留，评审精简视频会移除模型思考等待并提供时间映射。录屏无声，2 fps 不能用于高速物理动作或扬声器听感验收。只有一次出现的问题保持 `needs_review`。

[架构图](architecture.md) · [验证记录](validation.md) · [测试覆盖率](coverage.md)
