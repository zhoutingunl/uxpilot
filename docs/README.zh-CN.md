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

## Web 界面截图

[完整截图集](screenshots/README.md)覆盖首页、任务详情、分析回放、工具错误聚类、统计、部署记录及手机布局。图片采用独立的合成演示数据，不包含服务器私有运行证据。

![UX Pilot Web 首页](screenshots/home.png)

jfcs62 的兼容路由、持久状态迁移和发布方式见 [部署说明](../deploy/README.md)。

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

工作台现在默认选择**桌面 + Android 触屏模拟**，策略为**正常流程 + 中途切换、恢复与重试**。每个体验目标会在每个选中设备上独立执行；例如 2 个目标、2 个设备产生 4 次运行。设备之间独立，同一次连续流程的各阶段共享浏览器、Cookie、存储和本次房间。仅在目标里写“手机”不替代设备选择。

连续流程分为 `setup`、`transition`、`recovery`、`retry`：先建立运行中状态，再切页或打断，再尝试可见的停止/返回入口，最后重试目标。每阶段分别保存观察与验收，恢复成功不会覆盖首次失败；准备失败时依赖它的切换阶段会标为受阻，恢复阶段仍可尝试。阶段总计受任务步数和时限约束，未执行或预算耗尽会明确标为覆盖不完整。没有配置业务断言时，独立验收仍为 `not_verified`，模型逐阶段复核单独展示。规划依据不足时，报告首页会显示缺失的请求覆盖；状态链回归不等于穷尽全站所有页面和状态组合。

新增证据包括 `browser-environment.json`、`phase-results.json`、`phases/` 和 `invariant-checks.json`。手机运行会逐步检查布局视口是否被当作桌面缩放、横向溢出以及已聚焦输入框的遮挡，过程中出现的失败不会被最后恢复的页面覆盖。Android 使用触屏点击、滑动和按住操作；iPhone 模式使用 WebKit，无可用引擎时记录执行/覆盖失败，不降级成桌面冒充通过。工作台会禁用尚未安装的 WebKit 选项。

```bash
# 通用连续流程，默认也是这组设备与策略
uv run uxpilot review --repo /path/to/project --url https://demo.example.com/ \
  --device desktop --device android --coverage stateful --max-steps 24 --timeout 600

# 只测普通桌面流程
uv run uxpilot review --repo /path/to/project --url https://demo.example.com/ \
  --device desktop --coverage standard

# DuckKing：同房间四鸭导演运行中切到演示场景，再停止并重试
uv run uxpilot --config configs/duckking.yaml eval --task director-demo-recovery \
  --device desktop --device android
```

DuckKing 用例要求本阶段产生新的预录演出完成证据，不以旧记录、按钮点击或 HTTP 200 代替完成。不会通过换房间、清空存储或后台偷偷释放来恢复。`mobile-nod` 现在也使用 Android 触屏模拟，并把控件与画面同屏作为必需检查。`check-locales --viewport mobile` 同样升级为 Android 触屏配置。

**手机模拟仍不是真机验收。** 系统软键盘、动态地址栏、安全区、真实权限和硬件性能需要真机补验；横竖屏目前改变模拟视口，不代表物理旋转。WebKit 采用程序滚动，麦克风样本输入目前仅支持 Chromium；部分 WebKit 构建虽能产生触摸事件，`navigator.maxTouchPoints` 仍为 0，报告会保留实际值与限制。已有 API 请求不传 `devices`/`coverage` 时继续使用桌面/普通流程；新表单会明确提交选择。

报告保存 `report.html`、`report.md`、`report.json`，包含实际观察、推断、建议、证据、复现步骤和回归标准。源码定位在体验结束后进行，并验证文件、行号和引用原文；这只说明位置相关，不代表根因或部署版本已经确认。

自动任务有页面加载、JS 错误和横向溢出的基础检查。**没有业务断言时，业务独立验收显示 `not_verified`；不会因为网页能打开就宣布任务成功。** Judge 对业务是否完成的判断单独展示。可配置 `text`、`element_visible`、`url_contains` 等断言，或加入专用适配器。

公开资源的 GET/HEAD 默认允许，用于正常加载 CDN；导航仍限制在配置域名内。额外导航域名用 `--allow-host`，资源域名用 `--resource-host`。登录、验证码、第三方支付和复杂富文本操作不是保证开箱即用的范围，必要时记录受限并补充测试配置。

原始录屏保留，评审精简视频会移除模型思考等待并提供时间映射。DuckKing 新录屏会保存测试麦克风输入和网页 Web Audio 输出音轨；精简版按相同边界剪切音画，并保留完整语音时段。2 fps 不用于高速物理动作验收，数字音轨也不等于真实扬声器实录。只有一次出现的问题保持 `needs_review`。

## UI/UX Pro Max 优化建议

视频评审已接入本地 `ui-ux-pro-max` Skill 中适合录屏审查的 20 条规则，覆盖视觉层级、导航、表单、反馈、可读性和响应式布局。规则随项目打包，运行不依赖原来 `DuckKing` 目录的安装位置。本次接入的是评审规则子集，不包含完整 Skill 的检索引擎、配色字体库和设计系统生成器。

同一次 Judge 调用会同时返回 `issues` 和独立的 `ux_suggestions`。每条优化建议包含：实际观察、具体改法、预期收益（待验证）、优先级、证据把握、验收方式、证据文件、规则 ID 和视频时间点。HTML、Markdown、JSON 报告及单次分析回放都展示这些建议，建议保持 `needs_review`，不计入缺陷数量或改变任务验收。

默认开启，每次最多 3 条；项目 YAML 可配置：

```yaml
ux_review:
  enabled: true
  max_suggestions: 3  # 范围 1–5；enabled: false 可关闭
```

`ux-review-context.json` 保存成功评审的规则版本、来源指纹和内容哈希；重评时与旧判断一起归档。`ux-review-attempt.json` 记录最近一次尝试，重评失败时保留旧判断对应的规则。无效规则引用、缺失证据或越界视频时间点会被过滤，并记录在 `ux-suggestions-rejected.json`。主模型直接看视频，备用模型先看连续片段再综合，两条路径都支持建议；不会增加单独的建议模型调用，但会增加输入和输出 token。

建议只针对实际观察到的界面，不强制改换现有品牌风格。仅凭录屏无法证明 DOM/ARIA、精确对比度、点击区域尺寸、未操作的键盘行为和未测设备的表现，这些只能列为后续验收项。没有视频时仍可基于截图评审，并保留视频覆盖限制。已有任务配置和旧报告继续兼容。

## DuckKing 多语言与语音

当前 GLM-5.3-Flash / MiniMax-M3.1-Flash 评测接口发送文字、截图和视频，没有直接发送音频或执行 ASR。产品语音功能仍然可以测试：合成 WAV → 浏览器麦克风 → DuckKing 真实识别／灵矽 → 动作与回复 → 独立验收。评测模型看界面、转写和协议证据。人工报告使用带声视频，视觉 Judge 使用同画面的无声 judge.mp4 副本，不声称模型听过语音。

已将 MiniMax 独立语音服务接入评测流水线：`speech-2.8-hd` 生成中英法麦克风输入，`asr-1.0` 独立转写实际回复。配置 `speech` 后，`audio.source: minimax_tts` 自动生成循环 WAV；保留 `source: file` 的已有样本。凭据复用 `MINIMAX_CN_API_KEY` 或仓库外 `~/.secrets/minimax_api_key`，可使用 M Plan 订阅 Key。每项语音任务最多增加一次生成和一次转写调用，实际调用进入模型用量与事件记录。

```bash
# 中文本地优先、中文云端与聊天回退
uv run uxpilot --config configs/duckking.yaml eval \
  --task local-voice --task cloud-voice --task fallback-chat

# 英法界面切换、刷新保持，以及真实英法语音
uv run uxpilot --config configs/duckking.yaml eval \
  --task language-en --task language-fr \
  --task cloud-voice-en --task cloud-voice-fr

# 十个页面 × 桌面/手机，140 项导航、切换和刷新场景；不调用评测模型
uv run uxpilot --config configs/duckking.yaml check-locales

# 只复查一个页面的手机布局
uv run uxpilot --config configs/duckking.yaml check-locales --page index.html --viewport mobile
```

中文支持本地优先；英语和法语直接使用灵矽云端。配置通过 `?lang=zh|en|fr` 明确界面／下一次连接语言，合成样本与目标语种对应。界面切换用例检查文档语言和关键按钮翻译；语音用例分别检查连接请求语言、本轮识别文本、实际动作、回答文本、非静音 PCM、回答结束和浏览器播放完成。

`audio_received` 证明本轮收到有效非静音 PCM；`audio_played` 检查浏览器播放完成；`response_speech` 验证独立 ASR 中的配置关键词，报告提供转写、WAV 试听和 `speech-review.json`。只截取最后一次说话后的回复，混合采样率、截断或尚未播完的音频不会算作通过。`voice_session_language` 只验证请求语言。**关键词通过不代表完整语义、发音或语种准确率，物理扬声器及真人口音／噪声表现仍需独立验收。** 通用 `review` 自动生成任务时没有配置语音夹具，不能据此声称已覆盖语音。

`check-locales` 通过真实语言菜单检查中文、英语、法语的关键文案、当前选项、房间保持、原地切换、画布保留、英法云端模式限制、刷新与跨页保持，以及手机语言入口和横向溢出。先等待异步识别模式初始化完成，再验收状态。输出 `locale-report.html/json`、逐步截图、事件与录屏；覆盖十页的关键界面，不代表所有翻译均正确。

[架构图](architecture.md) · [验证记录](validation.md) · [测试覆盖率](coverage.md)

音轨来源、同步、兼容范围及历史录像限制见 [音频录制说明](audio-recording.md)。
