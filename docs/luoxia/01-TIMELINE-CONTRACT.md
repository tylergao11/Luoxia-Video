# 时间线契约 · timeline.json

> Schema：`contracts/timeline.schema.json`（JSON Schema draft 2020-12）
> 样例：`contracts/examples/timeline.example.json`（覆盖 rhythm / audio / 改写 / pinned 四种情况）

`timeline.json` 是整个 harness 的**唯一真源**。画面生成、字幕、剪辑合成全部对齐它。

**铁律：系统中不允许存在第二份时长来源。** 任何模块想知道"这个镜头多长"，只能读本文件，不能自己算、不能用函数默认值、不能从供应商返回值里取。

---

## 1. 存放位置

```
output/<episode_id>/timeline.json          # 工作副本
output/<episode_id>/timeline.frozen.json   # 冻结快照，只读
```

## 2. 两阶段提交模型

`phase` 字段驱动一个状态机，本质是把**廉价可逆**的工作和**昂贵不可逆**的工作切开：

```
draft ──► audio_locked ──► frozen ──► rendering ──► rendered
  │            │              │
  │            │              └─ 成本已估算并通过护栏，之后每一步都在花钱
  │            └─ 所有 TTS 已实测落盘，时长全部解算完毕（此时累计花费约等于零）
  └─ LLM 出的分镜草稿，台词可随意改
```

**廉价阶段（draft → audio_locked）**：LLM 拆剧本、TTS 合成、偏差调和、时长解算。全部代价是几分钱的文本和语音调用，agent 可以自由重跑、反复迭代，无需请示。

**冻结（audio_locked → frozen）**：校验全部不变量 → 计算成本估算 → 对照 `cost.budget_ceiling_usd` → 写入 `timeline_hash` 与 `frozen_at`。这是天然的人工审片卡点。

**昂贵阶段（frozen → rendered）**：出图、I2V、口型、合成。此阶段**禁止修改任何 `timing` 字段**。每次写回时间线前必须重新校验 `timeline_hash`，不一致即中止。

若确需改动时长，必须显式退回 `audio_locked`（记入 `audit`），重新解算并重新冻结。不允许就地偷改。

## 3. 三个时长字段，别搞混

这是整个契约里最容易写错的地方。

| 字段 | 类型 | 含义 |
|---|---|---|
| `audio.measured_duration_s` | 浮点 | TTS 产出音频的**实测**长度，必须由 ffprobe 探测真实文件得到 |
| `timing.target_duration_s` | 浮点 | 剪辑真正要用的长度 = `lead_in_s + measured_duration_s + tail_out_s` |
| `timing.request_duration_s` | **整数** | 向视频供应商请求的秒数 = `clamp(ceil(target), provider_min, provider_max)` |

**为什么要分开**：云端视频 API 只接受整数秒（Grok 是 1–15 整数，上游模型目录里 Wan/Kling/Seedance 也全是 `step: 1`）。所以"音频反推帧数"在云 API 上做不到精确，只能取整。

多出来的部分记在 `timing.slack_s = request_duration_s - target_duration_s`，由 `timing.trim` 声明如何吸收。

**禁止**用 `measured_duration_s` 直接去请求视频，也**禁止**把 `request_duration_s` 当作剪辑长度。前者漏掉了呼吸留白，后者会让整集逐镜变长、字幕全体漂移。

阶段二换本地模型后可直接控制 `num_frames`，届时 `request_duration_s` 退化为等于 `ceil(target)` 但 trim 精度提升到帧级。契约不需要变。

## 4. 不变量（校验器必须逐条检查）

以下每一条都可以机器校验，实现校验器时按此清单写：

1. `end_s - start_s == target_duration_s`（浮点容差 1e-6）
2. 相邻镜头首尾相接：`shots[i].timing.end_s == shots[i+1].timing.start_s`
3. `shots[0].timing.start_s == 0`
4. `index` 从 0 连续递增，与数组顺序一致
5. `slack_s == request_duration_s - target_duration_s`，且 `slack_s >= 0`
6. `trim.head_s + trim.tail_s == slack_s`（容差 1e-6）
7. `timing_driver == "audio"` 时，`audio.status` 必须为 `rendered` 且 `measured_duration_s` 非空
8. `timing_driver == "pinned"` 时，`target_duration_s == pinned_duration_s`
9. `audio.speed` 必须落在 `[global.min_speed_ratio, global.max_speed_ratio]` 内
10. `dialogue.rewrite_count <= 3`
11. `request_duration_s` 落在该 provider 声明的 `[min, max]` 内（Grok 为 1–15）
12. `dialogue.character_id` 必须能在 `cast` 中找到
13. `still.aspect_ratio == global.aspect_ratio`
14. `phase >= frozen` 时，`timeline_hash` 与 `frozen_at` 非空
15. 字幕区间必须包含在镜头区间内：`start_s <= subtitle.start_s` 且 `subtitle.end_s <= end_s`

## 5. 调和算法（solver）

对每个 `timing_driver == "audio"` 的镜头执行。这是把 audio-first 从口号变成代码的地方。

```
输入：shot（含 dialogue.text）、global 参数、provider 能力（min/max 秒）
输出：确定的 audio.* 与 timing.*

1. 以 speed = 1.0 合成 TTS，ffprobe 实测得 measured
2. target = lead_in + measured + tail_out
3. 若该镜头有美学期望时长 planned（来自分镜脚本），计算
      deviation = |target - planned| / planned
   若无 planned，跳到第 5 步（无偏差可言）

4. 按偏差分档处置，记入 timing.resolution_branch：
   a) deviation <= 0.15  → speed_adjust
        在 [min_speed_ratio, max_speed_ratio] 内解出 speed，重合成，回到第 1 步
   b) 0.15 < deviation <= 0.35 → llm_rewrite
        约束改写台词：保持语义与情绪，压缩到目标字数
        rewrite_count += 1，重合成，回到第 1 步
        rewrite_count 达到 3 仍不收敛 → 转 c)
   c) deviation > 0.35 → split_shot
        按语义边界（句号、语气转折）拆成两个镜头，各自重新走本算法
        拆分后新镜头继承 scene_id 与角色，index 重排

5. 量化：
      request = clamp(ceil(target), provider_min, provider_max)
   若 ceil(target) > provider_max（Grok 为 15）：
      硬性拆镜，无例外。禁止靠压语速把 16 秒的台词塞进 15 秒画面。

6. slack = request - target
   trim.strategy 默认 "tail"，trim.tail_s = slack
   （"hold_last_frame" 仅用于需要留住定格情绪的镜头，由分镜显式指定）

7. 排布主时间线：
      start_s = 前一镜头的 end_s（首镜为 0）
      end_s   = start_s + target
   注意用 target 而非 request 推进，否则整集会逐镜变长

8. 字幕：
      subtitle.start_s = start_s + lead_in
      subtitle.end_s   = subtitle.start_s + measured
```

### pinned 镜头的反向流程

`timing_driver == "pinned"` 时画面刚性，改为让音频适配：

```
1. target = pinned_duration_s（固定不动）
2. 可用语音时长 avail = target - lead_in - tail_out
3. 若 measured > avail：
     先提速，speed 上限 max_speed_ratio
     仍超出 → 约束改写台词（rewrite_count += 1）
     仍超出 → 报错并要求人工介入（不允许自动解除 pin）
4. 若 measured < avail：
     差额并入 tail_out，画面多留白即可，不需处理
5. resolution_branch = "pinned_fit"
```

### rhythm 镜头

无台词。`target = default_action_duration_s` 或分镜显式指定值，`request = ceil(target)`，其余同上。不涉及音频，`dialogue` 与 `audio` 字段留空。

## 6. 口型同步的触发规则

不做全片逐帧口型——太慢、容易崩脸，且行业实践证明没必要。工程标准是**句段级对齐 ±200ms**，观感上已消除绝大部分违和。

`lipsync.required` 仅在同时满足以下条件时置 true：

- `type == "dialogue"`，且
- `shot_size` 为 `close_up` 或 `extreme_close_up`，且
- `target_duration_s > 3.0`

其余镜头一律 `status: "skipped"`。口型是**可选后处理**，挂在主链路之外：先出成片片段，再对命中的镜头单独跑 LatentSync 并替换 `local_path`。口型失败不得阻塞整集合成。

## 7. 成本估算

冻结时计算，写入 `cost.estimated_usd`：

```
video_cost = Σ request_duration_s × rate(resolution)
image_cost = 输入图片数 × per_image_fee
estimated  = video_cost + image_cost
```

Grok 当前费率见 `02-PROVIDER-CONTRACT.md`。费率写在 provider 适配器里，不要散落在业务代码中。

`cost.budget_ceiling_usd` 是硬护栏：估算超限时 harness **必须停下等待人工确认**，不得自动开跑。这是无人值守模式下防止烧钱的最后一道闸。

`cost.actual_usd` 在渲染过程中累加，与估算的偏离超过 20% 应告警——通常意味着重试次数失控。

## 8. 幂等与重试

`audio.sha256` 是"台词 + 音色 + 语速"的内容哈希。重跑时哈希未变则跳过合成，这让廉价阶段可以放心反复执行。

视频侧以 `video.request_id` + `video.local_path` 判定：

- `local_path` 存在且文件校验通过 → 跳过，不重复付费
- `request_id` 存在但未落盘 → 先轮询原请求，**不要重新提交**
- `status == "failed"` 且 `attempts < 3` → 允许重试；错误码为 `invalid_argument` 时禁止原样重试，必须先修参数

## 9. 变更本契约的规矩

`schema_version` 遵循语义化版本。新增可选字段升次版本；删改字段语义、调整不变量属破坏性变更，升主版本并在本文件追加迁移说明。

改契约前先问一句：这个改动会不会让时长重新出现第二个来源？如果会，方案就是错的。
