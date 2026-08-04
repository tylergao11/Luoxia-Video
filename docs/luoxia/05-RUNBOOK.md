# 05 · 一键出片 Runbook

接上 API Key 后，用一条命令从小说文本到成片。

## 需要的 Key

| 变量 | 用途 | 是否必须 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | LLM 切片打分、TTS、静帧（Wanx） | 必须 |
| `XAI_API_KEY` | Grok 视频 | 想出动态视频时必须；没有则自动走静帧定格 |

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

# 没有 XAI 时自动 still-hold（静帧+音频合成）；也可强制：
python -m src.luoxia run your_novel.txt --still-hold

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
python -m src.luoxia render ep01          # 或 still-hold 路径走 run --still-hold
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
- `freeze` 超预算会停（exit code 2），不会偷偷开跑贵视频。
- `--max-repair-severity` 超标会停（`beats-select` exit code 3），停在花钱之前。

## 出片后先看这两处

1. **`beats.json` 的 `quality`。** `worst_severity` 是 `high` 说明成片里有程序编的台词——`invented_lines` / `truncated_lines` 告诉你有几句，`repairs[]` 告诉你是哪几段。这些地方八成读起来像旁白而不像人话，值得手改 `lines` 后重跑 `beats-select`。
2. **`output/<work_id>/characters/` 里的定妆图。** 这是全片锁脸的源头，它不对后面每一张都不对。改 `cast[].appearance` 会让缓存失效并重出；不动就一直复用，成本只付一次。

`cast[].appearance` 为空的角色不会有定妆图，`repairs` 里会有一条 `appearance_missing`，它的镜头会一张一个样。
