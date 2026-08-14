# Provider 接入规范 · 视频与语音后端

> 本文定义"如何把一个新的生成后端接进 harness"。阶段一接 Grok，阶段二换本地 Wan，走的是同一套规矩。
>
> 核心原则：**业务层不得出现任何厂商专有字段。** 违反这条，阶段二换本地模型时就要重写业务代码。

---

## 1. 边界在哪

```
pipeline / solver  ──只认──►  timeline.json 的 video.request（内部语义）
                                      │
                            ┌─────────┴─────────┐
                            │   Provider 适配器  │  ← 厂商字段只能出现在这一层
                            └─────────┬─────────┘
                                      ▼
                       xAI / DashScope / 本地 Wan 的原生 API
```

内部语义只有这几个概念：起始图、提示词、时长（整数秒）、分辨率、画幅比。适配器负责把它们翻译成厂商方言，并把厂商返回值翻译回契约字段。

`grok-imagine-video-1.5`、`request_id`、`respect_moderation` 这类词**只允许出现在 `src/models/grok.py` 内部**。

## 2. 上游的四个接入点

基线 `7a1213a` 实测结论。接任何新视频后端都是这四处，缺一不可：

### 2.1 `config/model_catalog/families/<family>.yaml`（新增）

声明模型族的能力与 UI 参数。草案见 `docs/luoxia/drafts/grok.yaml`。

改完必须依次跑：

```powershell
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

前者重新生成 `config/model_catalog/generated/model_catalog.json`，后者按 `config/model_catalog/schema/model-catalog.schema.json` 校验。**生成物必须一并提交**，否则运行时读到的还是旧目录。

### 2.2 `src/utils/provider_registry.py`（修改）

两处：

- `SUPPORTED_PROVIDER_BACKENDS` 增加 `"xai"`。当前值为 `("dashscope", "vendor", "mulerouter")`，不加进去 `register_family` 会直接抛 `ValueError`。
- `DEFAULT_PROVIDER_FAMILIES` 增加一条 `ProviderFamilyConfig`。

注册表按**模型名前缀**匹配，且按前缀长度**从长到短**排序命中。前缀必须写成 `"grok-imagine-video-"`，不要写成 `"grok"`——后者会把将来可能接入的 Grok 文本模型一起吃掉。

```python
ProviderFamilyConfig(
    model_family="grok-imagine-video-",
    backend_default="xai",
    backend_env_key="XAI_PROVIDER_MODE",
    credential_sources={"xai": ("XAI_API_KEY",)},
    supported_modalities=("t2v", "i2v", "r2v"),
    image_input_mode={"xai": "public_url"},
)
```

### 2.3 `src/models/grok.py`（新增）

实现 `src/models/base.py` 的 `VideoGenModel`：

```python
def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]
# 返回 (视频本地路径, API 耗时秒数)
```

注意返回值第二项是**接口耗时**（用于监控），不是视频时长。视频时长以契约中的 `request_duration_s` 为准，不要用返回值覆盖它。

### 2.4 `src/models/factory.py`（修改）

`ModelFactory.create_model` 是硬编码 if/elif 链，加一个分支：

```python
elif model_name.startswith('grok-imagine-video'):
    from .grok import GrokVideoModel
    return GrokVideoModel(config.get('model') or {})
```

### 陷阱：静默回退

`pipeline._resolve_video_backend` 在解析失败时**不报错**，直接 `return "dashscope"`：

```python
except (KeyError, ValueError):
    logger.debug("Provider backend not registered for video model %s, defaulting to dashscope.", model_name)
    return "dashscope"
```

如果前缀没注册对，Grok 的请求会被悄悄发去阿里云，表现为"莫名其妙的鉴权失败"或"用错了模型"。

**要求**：接入完成后必须写一条断言测试，验证 `_resolve_video_backend("grok-imagine-video-1.5") == "xai"`。不要依赖跑通一次就认为接对了。

## 3. Grok（xAI Imagine）接口事实

以下均来自 xAI 官方文档核对，不是推测。

### 3.1 端点

| 动作 | 方法与路径 |
|---|---|
| 提交生成 | `POST https://api.x.ai/v1/videos/generations` |
| 轮询结果 | `GET https://api.x.ai/v1/videos/{request_id}` |

鉴权：`Authorization: Bearer $XAI_API_KEY`。异步两步流程，提交后立刻返回 `{"request_id": "..."}`。

### 3.2 请求字段映射

| 契约内部语义 | Grok 字段 | 约束 |
|---|---|---|
| 模型 | `model` | `grok-imagine-video-1.5` |
| 提示词 | `prompt` | I2V 下可选；T2V/R2V 必填 |
| 起始图 | `image.url` 或 `image.file_id` | 二选一，JPEG/PNG/WebP；`url` 支持公网地址或 base64 data URL |
| 时长 | `duration` | **1–15 整数秒** |
| 分辨率 | `resolution` | `480p`(默认) / `720p` / `1080p` |
| 画幅 | `aspect_ratio` | 见下方警告 |

模式由字段组合决定，一次只能一种：`prompt` 单独 = T2V；`prompt + image` = I2V；`prompt + reference_images` = R2V。`image` 与 `reference_images` 同时出现返回 400。

### 3.3 响应

```json
{ "status": "done",
  "video": { "url": "https://vidgen.x.ai/.../video.mp4", "duration": 8, "respect_moderation": true },
  "model": "grok-imagine-video-1.5" }
```

`status` 取值：`pending` / `done` / `expired` / `failed`。

### 3.4 四个必须处理的坑

**坑一：成片默认自带音轨。** 官方明确"Generated videos include an audio track by default"。本 harness 自带 TTS，这条音轨是干扰项，必须剥离：

```
ffmpeg -i in.mp4 -c:v copy -an out.mp4
```

契约中 `video.has_audio_track` 与 `video.audio_stripped` 就是为此设的。**`has_audio_track == true` 且 `audio_stripped == false` 时，禁止进入合成阶段**，否则成片会出现两条人声。

**坑二：I2V 传 `aspect_ratio` 会拉伸源图。** 官方说明：I2V 默认沿用输入图比例，显式指定 `aspect_ratio` 会**覆盖并拉伸**。

做 16:9 横屏短剧的正确姿势是——**出图阶段就生成 16:9 的静帧，I2V 请求时省略 `aspect_ratio` 字段**。契约不变量第 13 条（`still.aspect_ratio == global.aspect_ratio`）就是这条规则的机器化保障。

**坑三：视频 URL 是临时的。** 官方标注为 temporary URL。必须在拿到后立即下载落盘，把 `local_path` 作为下游唯一引用，`source_url` 仅供排查。落盘后写 `fetched_at`。

**坑四：`respect_moderation` 为 false 表示被审核拦截。** 此时即使 `status == "done"` 也不能使用该 URL。映射到契约的 `video.moderation_passed`，为 false 按失败处理，且**不要原样重试**——重试同一个提示词只会再被拦一次，应先改写提示词。

### 3.5 错误码映射

| Grok `error.code` | 可否重试 | 处理 |
|---|---|---|
| `invalid_argument` | 否 | 参数或输入非法（含审核拦截）。必须先修参数/改写提示词 |
| `permission_denied` | 否 | Key 或团队无权限。中止并告警 |
| `failed_precondition` | 否 | 该模型不支持所请求的模式/分辨率。降级或换模型 |
| `service_unavailable` | 是 | 指数退避重试 |
| `internal_error` | 是 | 指数退避重试；持续失败需带 `request_id` 报 xAI |

鉴权失败、模型不存在、限流是**同步**返回的标准 API 错误，不会出现在 `error.code` 里，需要单独在 HTTP 层处理。

### 3.6 轮询策略

生成通常需要数分钟，随提示词复杂度、时长、分辨率上升。

- 轮询间隔 5 秒（官方 SDK 默认 100ms 过于激进，REST 手写不要照抄）
- 超时上限 15 分钟，超时后不重新提交，标记 `status: "expired"` 交由重试逻辑决策
- 多镜头并发提交，不要串行等待

### 3.7 计费

| 项目 | 单价 |
|---|---|
| 480p 输出 | $0.05 / 秒 |
| 720p 输出 | $0.07 / 秒 |
| 输入图片 | $0.002 / 张 |

费率表放在 `src/models/grok.py` 的模块常量里，供成本估算调用。**不要硬编码到 pipeline。**

一集 20 镜、平均 5 秒、720p 的粗估：`20 × 5 × 0.07 + 20 × 0.002 = $7.04`。抽卡重试会显著抬高实际值，这正是 `cost.budget_ceiling_usd` 存在的理由。

### 3.8 不要把视频模型自带音轨当配音

`reference_audios` 仅限美国地区的受信合作伙伴，且只能选内置 `voice_id`，**无法上传自有音频**。

视频生成接口的音轨不能承担本项目的配音角色，也无法替代“我方 TTS 音频驱动口型”。Luoxia 的默认配音走豆包 Seed-TTS 2.0，生成视频自带的音轨仍必须剥离；MuseTalk 只消费已经锁定的 Luoxia 音频。这印证了架构分层的必要性。

## 4. 阶段二：换本地 Wan 需要满足什么

只要阶段一守住了适配器边界，阶段二应当只做三件事：

1. 新增 `src/models/local_wan.py`，同样实现 `VideoGenModel`。
2. 新增 `config/model_catalog/families/local-wan.yaml`，声明本地能力。
3. `provider_registry` 增加 `"local"` 后端与对应 family。

业务代码与 `timeline.json` **一行都不该改**。

本地模型可直接控制 `num_frames`，届时可以让 `request_duration_s` 精确到帧。契约中 `target_duration_s`（浮点）与 `request_duration_s`（整数）的分离设计正是为此预留。

**验收标准**：把同一份 `timeline.frozen.json` 分别喂给 Grok 适配器和本地 Wan 适配器，除画面内容外，产出的时间结构（每镜起止、字幕时间轴、总时长）必须完全一致。做不到，说明边界被破坏了。

## 5. 语音后端

Luoxia 的唯一语音边界是 `src/luoxia/speech.py`，默认供应商为 `src/audio/doubao_tts.py`（豆包 Seed-TTS 2.0）；`src/audio/qwen3_tts.py` 与 `src/audio/xai_tts.py` 仅保留为显式兼容供应商。pipeline、CLI 和桌面 API 必须共用这组适配器，禁止各自形成第二条链路。旧版 `src/audio/tts.py` 只服务仍使用 DashScope 自定义音色的旧工作流。

自由文本导演意图、`speech_rate` 与 `pitch_rate` 先写入 provider-neutral 的 cast/audio 真相源，再由选定适配器编译为供应商参数；默认由豆包适配器执行，只有显式选择时才进入 Qwen3 或 xAI 兼容路径。缓存键必须覆盖最终供应商参数。**合成后必须用 ffprobe 探测真实音频时长**写入 `audio.measured_duration_s`，绝对不要用字数估算。
