# 落霞短剧 Harness · 总体思路与决策记录

> 本文只讲**为什么**。具体契约看 `01-TIMELINE-CONTRACT.md`，接入规范看 `02-PROVIDER-CONTRACT.md`，执行任务看 `03-HANDOFF-GROK45.md`。
>
> 阅读者：后续接手实现的 agent 与人类维护者。改动本项目架构前必须先读完本文，否则大概率会把 audio-first 改回 video-first。

---

## 1. 项目定位

把小说文本改编成可发布的横屏短剧（漫剧风格），**由 agent 无人值守驱动**，人类只在关键卡点审片。

不是做一个给人点按钮的创作工具。这个区别决定了后面几乎所有架构选择——人类工具可以把"对不齐了手动推一下"当作正常操作，agent 不能。

## 2. 为什么选 lumenx 作基座

上游：`alibaba/lumenx`，MIT License，2026-02 开源。本仓库已将其作为 `upstream` remote 拉入，基线提交 `7a1213a`。

选它的理由：

- **License 干净**。MIT，fork 后闭源商用无障碍。这是硬门槛。
- **它是 pipeline-first 的**，不是一堆脚本的集合。剧本分析、角色资产、分镜、视频、合成、导出是分离的阶段。
- **模型目录已经数据驱动**。`config/model_catalog/families/*.yaml` 定义每个模型族的能力与参数，配 JSON Schema 和校验脚本。这让"云 API 起步、后续换本地模型"的路线不需要重写业务代码。
- **上游本身就是 agent 协作开发的**，根目录有 `AGENTS.md`、`.claude/`、`.codex/`，代码组织对 agent 友好。

### 被否决的候选

| 项目 | 否决原因 |
|---|---|
| `Anning01/novelvids` | 仓库无 LICENSE 文件、README 无授权声明。法律上等于保留一切权利，fork 商用即侵权。 |
| `EvoLinkAI/ai-short-drama` | 声称 MIT，但本质是 EvoLink 聚合 API 的引流项目，地基绑定中间商。 |
| `xuanyustudio/LocalMiniDrama` | MIT 且轻量，是合格备选，但工程强度低于 lumenx。保留为 Plan B。 |
| `harry0703/MoneyPrinterTurbo` | MIT，工程分层值得借鉴，但它是素材拼接不是 AI 生成，链路不匹配。 |

## 3. 核心架构决策：audio-first

### 3.1 决策

**所有有台词的镜头，时长由 TTS 实测音频推导；视频生成只消费已确定的时长，不产生时长决策。**

### 3.2 论据

**语音是刚性约束，画面是弹性变量。** 一句台词的时长由音素决定，语速调整超过 ±10% 人耳即可分辨。同一镜头给 4 秒还是 6 秒，观众无感。工程上遇到一刚一弹两个约束，先解刚性的，让弹性的适配。

**成本不可逆。** TTS 是几分钱、几秒钟；I2V 是几毛到几块、几分钟 GPU，且需要抽卡重跑。视频生成是链路上最贵且最不可逆的一步，必须最后一个被确定参数。先冻结廉价的约束图，再调用昂贵的生成器。

**口型是硬性数据依赖。** lip-sync 模型的输入就是音频，S2V 模式同样以音频为输入。没有音频，特写对白镜在 DAG 上根本无法开始。短剧是台词密集型题材，这类镜头占比很高。

**漂移会累积，而 agent 没有眼睛。** video-first 的失败模式是每镜超出一点、逐镜累加，一集中段开始明显崩。人类可以在时间线上手动推，agent 不能。video-first 等于把"对不齐怎么办"推到一个没有人的环节。audio-first 把它变成一次可计算的确定性调度。

### 3.3 例外：三种时长驱动方式

audio-first 不是"一切都由音频推导"。契约里用 `timing_driver` 区分三种情况：

- **`audio`（默认）**——有台词的镜头。时长 = 前导 + 实测音频 + 留白。
- **`rhythm`**——无台词镜头（打斗、空镜、转场、情绪镜头）。没有音频约束，时长由剪辑节奏和模型稳定区间决定（I2V 普遍 3~6 秒最稳）。硬套音频时长没有意义。
- **`pinned`**——画面已成为不可再生资源（抽卡多次才达标、或使用既有素材）。此时画面变刚性，反向让音频适配：先压语速，不够再改写台词。

这是本架构与"无脑 audio-first"的区别：**默认音频刚性，但支持逐镜反转。**

## 4. 上游现状与差距

以下是对基线 `7a1213a` 的实测结论，不是推测。

### 4.1 上游是 video-first 的

`src/apps/comic_gen/pipeline.py` 中：

```
def create_video_task(self, script_id, image_url, prompt, duration: int = 5, ...)
```

时长是硬编码的函数默认参数。TTS 在 `src/apps/comic_gen/audio.py`（仅 273 行）中**事后**按帧生成，再用 ffmpeg 的 `adelay` + `amix` 把配音贴到已生成的画面上。全仓库没有任何一处从 TTS 实测时长反推视频时长。

`pipeline.py` 4881 行 vs `audio.py` 273 行，这个比例本身说明音频在上游架构里是配角。

**这是本项目要做的核心手术：把时序控制权从视频侧夺回给音频侧。**

### 4.2 provider 抽象是半成品

需要澄清一个容易误判的点：模型目录（YAML + Schema + 校验脚本）确实是数据驱动的，但**真正的视频后端派发是硬编码的**。

- `src/models/base.py`：`VideoGenModel` 抽象基类，仅一个方法 `generate(prompt, output_path, **kwargs) -> (path, duration)`。
- `src/models/factory.py`：`ModelFactory.create_model` 是 if/elif 链，按模型名硬匹配。
- `src/utils/provider_registry.py`：按模型名**前缀**路由的注册表，但 `SUPPORTED_PROVIDER_BACKENDS` 只有 `("dashscope", "vendor", "mulerouter")` 三个后端。
- `pipeline._resolve_video_backend` 在解析失败时**静默回退到 dashscope**。

结论：接入新 provider 是小改动但有固定套路，共四个接入点，详见 `02-PROVIDER-CONTRACT.md`。那个静默回退是个坑——接 Grok 时若前缀没注册对，请求会被悄悄发去 dashscope 而不是报错。

### 4.3 断点续跑被刻意关闭

`pipeline.py` 有完整的任务状态机（`pending → processing → completed/failed`）、JSON 落盘、孤儿任务回收 `_recover_orphan_tasks()`，但注释明确写着不自动续跑，因为重跑要花钱。

对人类用户这是保护；对无人值守 agent 这是障碍。我们需要的是**幂等重试 + 成本护栏**，而不是不重试。

### 4.4 TTS 绑死 DashScope

> 本节记录最初审计时的上游状态。当前 Luoxia 已改为 `src/luoxia/speech.py` 单一语音边界，默认使用豆包 Seed-TTS 2.0；Qwen3-TTS、xAI TTS 与旧 `src/audio/tts.py` 只保留给显式兼容工作流。

最初 `src/audio/tts.py` 的 `TTSProcessor` 走 `dashscope` SDK（CosyVoice 与 Qwen3-TTS 两条路径），读 `DASHSCOPE_API_KEY`，且各入口会自行实例化供应商。

### 4.5 维护风险

`pipeline.py` 4881 行、`api.py` 3986 行，都是巨型文件。改动后基本无法干净合并上游更新。

**策略：不追 upstream。** 基线一次性锁死在 `7a1213a`，自有改动尽量落在新增文件中，必须修改上游文件时保持最小 diff 并在本文档登记。upstream remote 只作参考与查证用途。

## 5. 分阶段路线

### 阶段一（当前）：云 API 跑通效果

- 视频：**Grok（xAI Imagine）**，`grok-imagine-video-1.5`，I2V 为主。
- TTS：本地 Qwen3-TTS VoiceDesign（音频先锁定，云视频不再决定声音）。
- 目标：跑通 audio-first 全链路，验证成片质量与单集成本。

选 Grok 起步是因为按秒计费、时长参数为 1–15 秒整数、I2V 接口简单，适合验证时序模型是否正确。

### 阶段二：换本地模型控成本

- 视频：本地 Wan 2.7（Apache 2.0，权重可商用可微调）。
- TTS：本地 Qwen3-TTS（Apache 2.0）。
- 口型：MuseTalk 1.5（代码 MIT，模型允许商业使用；第三方依赖仍各自遵守许可证）。

阶段二能否低成本完成，取决于阶段一是否严格遵守了 `02-PROVIDER-CONTRACT.md` 的适配器边界。**业务代码里出现任何 xAI 专有字段，都是在给阶段二埋债。**

本地模型还有一个额外好处：可直接控制 `num_frames`，从而摆脱云 API 的整数秒量化限制，做到帧级精确对齐。契约中 `target_duration_s`（浮点）与 `request_duration_s`（整数）分离，正是为这一天预留的。

## 6. 授权红线

项目本身 MIT 不代表安全，真正的商业条款风险在模型权重。

**可用**：Wan 2.7 / 2.2（Apache 2.0）、Qwen3-TTS（Apache 2.0）、CosyVoice（Apache 2.0）、GPT-SoVITS（MIT）、MuseTalk（代码 MIT，模型允许商业使用）。

**需逐条审**：IndexTTS 使用 bilibili 自定义 Model Use License，不是 Apache。

**禁用**：ChatTTS、Fish-Speech、F5-TTS 等 CC-BY-NC 系；Wav2Lip（研究用途）。FLUX.1 `[dev]` 历史上为 Non-Commercial License（Apache 2.0 的是 `[schnell]`），引入前必须重新核对原文。

**其他**：ComfyUI 是 GPL-3.0，只能作为独立进程走 HTTP 调用，不得作为库链入产品。Apache 2.0 不提供 indemnification，训练数据版权风险由使用方承担。

## 7. 决策速查

| 决策 | 结论 | 不可动摇的理由 |
|---|---|---|
| 基座 | fork `alibaba/lumenx` @ `7a1213a` | MIT + pipeline-first + 数据驱动模型目录 |
| 时序 | audio-first，支持逐镜 pinned 反转 | 音频刚性、成本不可逆、口型硬依赖、agent 无法目测纠偏 |
| 真源 | `timeline.json` | 画面、字幕、剪辑全部对齐它，不允许第二份时长来源 |
| 提交模型 | 两阶段：廉价阶段全解算 → 冻结 → 昂贵阶段执行 | 掏钱前必须知道要花多少 |
| 上游 | 锁死基线，不追更新 | 巨型文件无法干净合并 |
| provider | 适配器边界严守，业务层不见厂商字段 | 阶段二要换本地模型 |
