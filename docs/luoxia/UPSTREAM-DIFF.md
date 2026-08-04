# Upstream Diff Log

基线：`alibaba/lumenx` @ `7a1213a`。下列文件为允许清单内的最小改动。

| 文件 | 原因 | 影响面 |
|---|---|---|
| `src/utils/provider_registry.py` | 增加 `xai` 后端与 `grok-imagine-video-` family，否则 Grok 无法路由 | 仅扩展注册表；未改既有 family 行为 |
| `src/utils/model_catalog.py` | `SUPPORTED_PROVIDER_BACKENDS` 增加 `xai`，否则 grok.yaml 无法通过 catalog build | 与 provider_registry 对齐；无路由行为变化 |
| `src/models/factory.py` | 增加 Grok 分支；兼容 `model.name` 与嵌套 `model.name` | 仅新增分支 |
| `src/models/grok.py` | xAI 视频适配器；本地静帧转 data URI 供 I2V | 新文件；不改其它供应商 |
| `src/apps/comic_gen/pipeline.py` | 去掉 `duration=5` 硬默认；timeline 优先；Grok 禁止静默回退 dashscope | Studio API 若未传 duration 会报错（应显式传）；Luoxia 以 timeline 为准 |
| `src/audio/tts.py` | 增加 `content_sha256` / `synthesize_measured`（ffprobe 实测 + 缓存） | 不改既有 `synthesize` 签名 |
| `config/model_catalog/families/grok.yaml` + generated catalogs | 数据驱动声明 Grok 能力与时长范围 | UI 可选到 Grok 模型 |

上游 `src/models/image.py` 未改动。`WanxImageModel.generate` 已原生支持 `ref_image_paths`，并在有参考图时自动切到 `i2i_model_name`（默认 `wan2.7-image`），锁脸直接复用该行为。
