"""Provider defaults shared by the URL/repository entry point and project profiles."""

from .schema import Models, Provider


def default_models() -> Models:
    return Models(
        primary=Provider(
            name="zhipu",
            model="glm-5.3-Flash",
            endpoint="https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
            key_env="ZHIPU_API_KEY",
            key_file="~/.secrets/zhipu_api_key",
            timeout_seconds=240,
            max_tokens=8192,
            request_options={"thinking": {"type": "disabled"}},
        ),
        fallback=Provider(
            name="minimax",
            model="MiniMax-M3.1-Flash",
            endpoint="https://api.minimaxi.com/v1/chat/completions",
            key_env="MINIMAX_CN_API_KEY",
            key_file="~/.secrets/minimax_api_key",
            timeout_seconds=240,
            max_tokens=16384,
            stream=False,
            video_chunk_seconds=4,
            request_options={
                "thinking": {"type": "adaptive"},
                "reasoning_split": True,
                "reasoning_effort": "low",
            },
        ),
        max_calls_per_run=40,
    )
