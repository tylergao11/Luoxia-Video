# 05 · 一键出片 Runbook

接上 API Key 后，用一条命令从小说文本到成片。

## 需要的凭据

| 变量 | 用途 | 是否必须 |
| --- | --- | --- |
| 订阅池登录或 `XAI_API_KEY` | Luoxia LLM、图片与 Grok 视频，取决于当前鉴权模式 | 必须二选一 |
| `output/runtime/qwen3-tts` | 本地 Qwen3-TTS VoiceDesign 配音 | 可选离线替代；默认 xAI TTS 不依赖此目录 |
| `output/runtime/musetalk` | MuseTalk 1.5 音频驱动口型 | 特写对白镜必需 |
| `DASHSCOPE_API_KEY` | 仍选择 DashScope 的旧工作流或模型 | Luoxia 主链不需要 |

**视频凭据有两条路**，管线问的是鉴权层（`src/auth`）而不是环境变量：

- `session` 模式（默认）：登录订阅池，`XAI_API_KEY` 留空甚至注释掉都没关系
- `api_key` 模式：`LUOXIA_AUTH_MODE=api_key` + 设置 `XAI_API_KEY`

查当前状态：`python -c "from src.auth.resolver import status; print(status())"`。

新版 Grok CLI 的 OAuth 会话位于 `~/.grok/auth.json`；鉴权适配器读取最新的该文件，旧版 `~/.Doggy/auth.json` 只作为历史位置兜底。网页重新登录后如果状态仍显示过期，先确认程序没有继续读取旧文件。

凭据取不到时管线**直接报错停下**，不会降级出片。曾经的行为是缺 `XAI_API_KEY` 就悄悄把静帧定住几秒当成片——一个登录着订阅池的账号会被误判成"没配置"，最后拿到一段不会动的幻灯片，而 phase 照样写 `rendered`、退出码照样是 0。静帧定格模式已经删除：**没有真视频就不出片**。

`lipsync.required=true` 同样是硬合同：缺视频、缺锁定音频、MuseTalk 运行时不完整或推理失败时，管线记录失败镜头后停止，禁止绕过口型继续合成成片。

`.env` 由 `src/luoxia/env.py` 在 CLI / 管线入口加载。本地 Qwen3-TTS 运行时或模型缺失时，TTS 会明确失败；禁止用静音、音调或默认音色冒充成功。

可选 LLM 切换（默认 DashScope）：

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

## 一键跑

```bash
# 建议先把样例拷出去改
python -m src.luoxia --output-root output/luoxia_runs run contracts/examples/novel.sample.txt --work-id demo --budget 5

# 第一次跑真 Key 时建议加严格门：程序编台词就停，别把钱花在编出来的内容上
python -m src.luoxia run your_novel.txt --max-repair-severity medium
```

产物：

```
output/<work_id>/beats.json
output/<work_id>/characters/*.png     # 每角色一张定妆图，全片锁脸的底图
output/<episode_id>/timeline.json
output/<episode_id>/audio/*.wav
output/<episode_id>/stills/*.png
output/<episode_id>/video/*.mp4
output/<episode_id>/final.mp4
```

默认可断点续跑；加 `--no-resume` 强制重来。

## 分步（调试用）

```bash
python -m src.luoxia analyze novel.txt --work-id demo
python -m src.luoxia beats-select output/demo/beats.json --max-repair-severity medium
python -m src.luoxia sheets output/demo/beats.json
python -m src.luoxia beats-bridge output/demo/beats.json ep01
python -m src.luoxia solve ep01
python -m src.luoxia stills ep01
python -m src.luoxia freeze ep01
python -m src.luoxia render ep01
python -m src.luoxia compose ep01
```

## 流水线顺序

```
analyze → select → [quality gate] → character sheets → bridge
    → polish prompts → solve(TTS) → stills(I2I 锁脸)
    → freeze(cost gate) → video → compose
```

- **beats** 决定砍什么；**timeline** 决定多久。
- 时长只来自 TTS 实测，不来自字数估算。
- 表演只来自 `dialogue.performance`（旧文件回退到 `dialogue.emotion`）；供应商标签不进入字幕和口型文本。
- `freeze` 超预算会停（exit code 2），不会偷偷开跑贵视频。
- `--max-repair-severity` 超标会停（`beats-select` exit code 3），停在花钱之前。

## 出片后先看这两处

1. **`beats.json` 的 `quality`。** `worst_severity` 是 `high` 说明成片里有程序编的台词——`invented_lines` / `truncated_lines` 告诉你有几句，`repairs[]` 告诉你是哪几段。这些地方八成读起来像旁白而不像人话，值得手改 `lines` 后重跑 `beats-select`。
2. **`output/<work_id>/characters/` 里的定妆图。** 这是全片锁脸的源头，它不对后面每一张都不对。改 `cast[].appearance` 会让缓存失效并重出；不动就一直复用，成本只付一次。

`cast[].appearance` 为空的角色不会有定妆图，`repairs` 里会有一条 `appearance_missing`，它的镜头会一张一个样。
