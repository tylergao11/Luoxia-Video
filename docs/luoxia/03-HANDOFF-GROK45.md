# 执行交接书 · 致后续实现 agent（grok-4.5）

> 你负责**实现**。架构决策已经做完，不需要你重新论证，也不允许你推翻。
> 如果你认为某项决策是错的，**停下来提出，不要边写边改架构**。

---

## 0. 必读顺序

1. `docs/luoxia/00-DESIGN-RATIONALE.md` —— 为什么这么设计，尤其第 3 节 audio-first 的论据
2. `docs/luoxia/01-TIMELINE-CONTRACT.md` —— 你要实现的核心契约
3. `contracts/timeline.schema.json` + `contracts/examples/timeline.example.json` —— 机器可校验的定义与标准答案
4. `docs/luoxia/02-PROVIDER-CONTRACT.md` —— Grok 怎么接
5. 本文

上游自带的 `AGENTS.md` 也读一遍，但**本文与上游 AGENTS.md 冲突时以本文为准**。

## 1. 项目一句话

把小说改编成横屏漫剧短剧，agent 无人值守驱动，人类只在冻结点审片。基座是 fork 自 `alibaba/lumenx` 的 MIT 项目，基线锁死在 `7a1213a`。

阶段一用 Grok（xAI）云 API 跑通，阶段二换本地 Wan 控成本。

## 2. 环境事实

- 操作系统 Windows 10，shell 是 **PowerShell**（不是 bash，官方文档里的 curl/jq 示例不能直接跑）
- 仓库根目录 `D:\Luoxia-Video`，git 已初始化，`upstream` 指向 `alibaba/lumenx`
- 后端 Python（`requirements.txt` / `pyproject.toml`），前端 Next.js 在 `frontend/`
- 仓库自带 `bin/ffmpeg`，优先用它，不要假设系统 PATH 里有 ffmpeg
- 需要的凭据：订阅池登录，或 api_key 模式下的 `XAI_API_KEY`（Luoxia 图片 / 视频；session 也可供 LLM）。默认 TTS 为本地 Qwen3-TTS，不需要云密钥；仅旧 DashScope/默认 LLM 路径需要 `DASHSCOPE_API_KEY`。写进 `.env`，参考 `.env.example`。**任何情况下不得把密钥写进代码或提交**

## 3. 硬性禁令

违反以下任意一条，工作视为不合格：

1. **不得引入第二个时长来源。** 任何模块想知道镜头多长，只能读 `timeline.json`。禁止函数默认值 `duration=5`，禁止从供应商返回值反写时长。
2. **不得用字数估算音频时长。** 必须 ffprobe 探测真实文件。整个架构的地基是这个实测值。
3. **不得把厂商字段泄漏到业务层。** `grok-imagine-video-1.5`、`request_id`、`respect_moderation` 只能出现在 `src/models/grok.py` 内。
4. **不得在 `frozen` 之后修改任何 `timing` 字段。** 需要改就显式退回 `audio_locked` 并记入 `audit`。
5. **不得绕过成本护栏。** 估算超过 `cost.budget_ceiling_usd` 必须停下等人工确认。
6. **不得把 audio-first 改回 video-first**，包括"临时先这样跑通"。
7. **不得通过 PowerShell 内联命令传递中文文本**（`echo`、`Set-Content`、`Out-File`、重定向、heredoc 都不行，会产生乱码）。中文一律通过文件编辑工具写入，或由 Python/Node 脚本以显式 UTF-8 读写。写完做一次乱码扫描。
8. **不得改动上游文件超出必要范围。** 允许修改的上游文件仅限第 5 节列出的那几个，且保持最小 diff。新增逻辑优先放新文件。

## 4. 文件编码

所有文本文件 **UTF-8 无 BOM**。JSON、Markdown、Python、YAML 一律如此。

写完含中文的文件后，扫描是否出现替换字符、连续问号、以及典型乱码片段。发现即视为写入通道有问题，修通道而不是手工改字。

## 5. 允许修改的上游文件

| 文件 | 允许的改动 |
|---|---|
| `src/utils/provider_registry.py` | 增加 `"xai"` 后端与 Grok family 配置 |
| `src/models/factory.py` | 增加 Grok 分支 |
| `src/apps/comic_gen/pipeline.py` | 时长来源改为读契约；接入冻结校验 |
| `src/audio/tts.py` | 增加 ffprobe 实测时长 |
| `config/model_catalog/` | 新增 grok family 并重新生成目录 |

其余一律新增文件。每次修改上游文件，在 `docs/luoxia/UPSTREAM-DIFF.md` 追加一行：文件、原因、影响面。这个文件由你创建并维护。

## 6. 任务分解

按依赖顺序执行，**不要跳步**。每个任务完成后先自测，再进下一个。

### T1 · 契约校验器

新增 `src/luoxia/timeline/validator.py` 与 CLI 入口。

实现 `01-TIMELINE-CONTRACT.md` 第 4 节全部 15 条不变量，外加 JSON Schema 校验。

**完成标准**：`contracts/examples/timeline.example.json` 通过全部校验；针对每一条不变量各写一个故意违反的用例并断言报错，错误信息要指明是哪个 `shot_id` 的哪一条。

先做校验器是因为它是可执行的规格说明，后面所有任务都拿它当验收标尺。

### T2 · 音频时长实测

改造 `src/audio/tts.py`：合成后用 `bin/ffmpeg` 附带的 ffprobe 探测真实时长，写入契约的 `audio.measured_duration_s`；同时算出 `audio.sha256`（台词 + 音色 + 语速的内容哈希）。

**完成标准**：同一段台词重复调用第二次直接命中缓存跳过合成；实测时长与播放器读数一致。

### T3 · 调和 solver

新增 `src/luoxia/timeline/solver.py`，实现 `01-TIMELINE-CONTRACT.md` 第 5 节算法，含三种 `timing_driver` 与 pinned 反向流程。

注意几个易错点：主时间线推进用 `target_duration_s` 而非 `request_duration_s`；`ceil(target)` 超过 provider 上限时必须拆镜，不许压语速硬塞；改写次数上限 3 次后转拆镜。

**完成标准**：给一段含长短句混排的测试剧本，产出的 `timeline.json` 通过 T1 校验器；四种 `resolution_branch` 都有测试覆盖。

### T4 · 冻结与成本护栏

实现 `audio_locked → frozen`：校验全部不变量 → 计算 `cost.estimated_usd` → 对照 `budget_ceiling_usd` → 写 `timeline_hash` 与 `frozen_at` → 输出 `timeline.frozen.json`。

**完成标准**：超预算时明确拒绝并给出人类可读的成本明细（每镜秒数、单价、小计）；冻结后篡改任一 `timing` 字段，再次校验必须失败。

### T5 · Grok 适配器

按 `02-PROVIDER-CONTRACT.md` 第 2 节四个接入点实施，加 `src/models/grok.py`。

必须处理第 3.4 节四个坑：剥离默认音轨、I2V 省略 `aspect_ratio`、临时 URL 立即落盘、`respect_moderation` 为 false 按失败处理且不原样重试。错误码按 3.5 节映射，轮询按 3.6 节。

**完成标准**：存在断言测试 `_resolve_video_backend("grok-imagine-video-1.5") == "xai"`（防静默回退到 dashscope）；单镜头真实跑通一次，产出文件无音轨、比例未被拉伸、`cost_usd` 已记录。

### T6 · 渲染阶段消费契约

改造 `pipeline.py`：视频任务的时长**只读自** `timeline.json`，删除 `duration: int = 5` 这类默认值来源。每次写回前校验 `timeline_hash`。

**完成标准**：全仓库检索不到"业务层自行决定视频时长"的代码路径。

### T7 · Headless CLI 与幂等续跑

新增 CLI 入口，支持不启动 webview 桌面窗口跑完整链路（上游 `main.py` 起的是 webview + uvicorn，不能直接用于 agent）。

实现幂等续跑：`local_path` 存在且校验通过则跳过；`request_id` 存在但未落盘则**轮询原请求而非重新提交**；失败重试上限 3 次且 `invalid_argument` 不原样重试。

**完成标准**：整集跑到一半强杀进程，重启后不重复付费、不重复提交、能跑完。

### T8 · 合成对齐

按契约的 `trim` 与 `subtitle` 字段做最终合成：吸收 `slack_s`、贴字幕、拼接。

**完成标准**：成片总时长等于 `shots[-1].timing.end_s`（容差 100ms）；抽查任意三镜，字幕起止与人声起止对齐在 ±200ms 内。

### T9 · 口型后处理（最后做）

对 `lipsync.required == true` 的镜头用本地 MuseTalk 1.5 消费已锁定音频，并替换 `local_path`。

**完成标准**：口型失败不阻塞整集合成。

## 7. 测试要求

- 每个任务都要有可重复运行的自动化测试，放在 `tests/luoxia/`
- 涉及外部 API 的测试用录制/桩数据，不要每次跑都真实烧钱
- 真实 API 只在明确的冒烟测试里调用，且默认跳过，需显式开关启用
- 不要用"跑通一次"代替测试

## 8. 提交规范

- 小步提交，一个任务一个或多个提交，提交信息说明**为什么**而不只是改了什么
- 修改上游文件的提交单独拆出来，便于日后审计
- 不提交 `.env`、密钥、`output/` 产物、模型权重

## 9. 卡住了怎么办

以下情况**立即停下来问，不要自行发挥**：

- 契约与实现冲突，且你认为契约错了
- 需要修改第 5 节允许清单之外的上游文件
- Grok 实际接口行为与 `02-PROVIDER-CONTRACT.md` 记载不符（文档基于 2026-08-04 的官方说明，接口可能已变）
- 成本估算显著超出预期
- 连续两次修复同一个问题失败 —— 此时停止打补丁，转做根因分析

报告时给出：你想做什么、卡在哪、你已经试过什么、你建议的两三个选项。
