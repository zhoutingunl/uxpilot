# 语音输入夹具

2026-11-03 使用 macOS Tingting（中文）合成。单声道 48 kHz PCM16 WAV，前置 2 秒静音、完整周期 12 秒。浏览器原生 fake audio capture 循环播放；Agent 按麦克风获取时刻对齐到周期 1.5 秒，持有 3.2 秒（点头）或 6.2 秒（长句）。

- nod-loop.wav：点头。
- sequence-loop.wav：先点头，再向前走。
- chat-loop.wav：你好小鸭，你现在感觉怎么样？

2026-11-07 新增英语和法语样本，同样为单声道 48 kHz PCM16、前置 2 秒静音、12 秒周期：

- nod-en-loop.wav：Please nod your head.（macOS Samantha；`start_ms: 1500`、`hold_ms: 4200`）
- nod-fr-loop.wav：Fais oui de la tête, s'il te plaît.（macOS Thomas；`start_ms: 1500`、`hold_ms: 5200`）

两项英法语用例通过 `?lang=en|fr` 指定界面和下一次灵矽会话语言，直接使用云端，不把仅支持中文的本地动作识别当作英法识别器。音频窗口测试检查完整有效语音落在按住区间内；固定样本不代表通用识别准确率。

输入是系统合成语音，后端识别、动作和云端回答为真实。新增音频应同时更新配置中的 transcript、start_ms/hold_ms/loop_ms，保证不截断语句或录到下一周期。
