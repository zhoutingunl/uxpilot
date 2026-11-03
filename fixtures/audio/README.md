# 语音输入夹具

2026-11-03 使用 macOS Tingting（中文）合成。单声道 48 kHz PCM16 WAV，前置 2 秒静音、完整周期 12 秒。浏览器原生 fake audio capture 循环播放；Agent 按麦克风获取时刻对齐到周期 1.5 秒，持有 3.2 秒（点头）或 6.2 秒（长句）。

- nod-loop.wav：点头。
- sequence-loop.wav：先点头，再向前走。
- chat-loop.wav：你好小鸭，你现在感觉怎么样？

输入是系统合成语音，后端识别、动作和云端回答为真实。新增音频应同时更新配置中的 transcript、start_ms/hold_ms/loop_ms，保证不截断语句或录到下一周期。
