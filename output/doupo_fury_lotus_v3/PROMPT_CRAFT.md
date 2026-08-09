# 佛怒火莲 v3 · Grok 视频 Prompt 工艺（反慢动作）

## 现象

`grok-imagine-video-1.5` 在「大场面 / 特效 / 花系意象 / cinematic」镜头里，**默认会把动作拉成慢动作或漂浮芭蕾**，像预告片花絮，不像正常人眼看的实战。

旧 plan 踩过的雷：

| 写法 | 模型常见解读 |
| --- | --- |
| `cinematic` + 长时长 (4–6s) 却事件不够密 | 用慢漂、空镜、花瓣转圈填时长 |
| `speed ramp` | 先慢后快的预告片节奏，不是全程实时 |
| `petals counter-rotate` / 花开花合装饰描写 | 莲花缓慢开合，像 MV |
| `graceful / floating / ethereal / lingering` | 直接慢动作 |
| 一镜塞 4–5 个事件 | 每个事件被拉长，整体发黏 |
| 只写 `no slow motion` 不给**正速度锚** | 负向不够，仍慢 |

## v3 硬规则

1. **原子镜**：动作峰值尽量 **1–2s**；只有余波允许 2s。不要用 4–6s 单镜硬塞整场高潮。
2. **每镜必须有 TEMPO LOCK**（英文，靠前）：
   - 正向：`REAL-TIME combat tempo` / `normal 1x physics` / `live-action fight VFX plate` / `high action density` / `every 0.1s shows visible change`
   - 负向：`NO slow motion, NO bullet time, NO time dilation, NO dreamy float, NO balletic petal dance, NO lingering beauty holds, NO speed-ramp trailer pacing`
3. **START → END 状态机**：写清起点姿态 / 终点姿态 / 物体位移距离，逼模型推进，而不是在起点附近漂。
4. **秒级时间表**（只写 1–3 个节拍，别写小说）：
   - 例：`0.0–0.3s snap compress; 0.3–1.0s full-speed launch and separation`
5. **物理动词**优先：`snap / whip / slam / punch / crack / blast / streak / recoil`  
   少用：`swirl gently / bloom / unfurl / dance`
6. **火莲当弹药，不当装饰**：强调 `coherent projectile / compact warhead / ballistic path`，禁止「花瓣慢转展示」。
7. **Hit-stop 只能 1–2 帧**，且必须写 `then IMMEDIATELY resume full real-time`。
8. **镜头**：`snappy whip-pan / hard jolt / aggressive track`；禁止 `slow push-in / gentle orbit` 用于动作镜。
9. **时长与密度匹配**：1s 镜只许 1 个主事件；2s 镜最多 2 个。事件不够就**拆镜**，不要灌慢动作。
10. **I2V**：静帧先锁构图与能量姿态；视频 prompt 只负责**从静帧往终点推进**，不要重新描写世界观长文。

## 流程（本仓库）

```
stills (xAI image) → i2v (grok-imagine-video) → concat
scripts/gen_doupo_fury_lotus_v3_stills.py
scripts/gen_doupo_fury_lotus_v3_video.py
```

- 风格：红果 AI 漫 · 精致 3D CGI（见 `docs/luoxia/07-STYLE-REF.md`）
- 视频模型：`grok-imagine-video-1.5`，默认 `1080p`（与 720p 同价时取高）
- 音频：prompt 可写 native SFX（利于运动耦合）；成片链路仍可按业务剥离音轨后贴 TTS

## 验收（肉眼）

- [ ] 蓄力压缩像「加压」，不是花瓣展览
- [ ] 出手瞬间有后坐力，火莲离开手掌有清晰空隙
- [ ] 飞行全程高速，目标迅速变大，无漂浮感
- [ ] 撞击只有极短顿挫，立刻炸开
- [ ] 爆炸前 0.5s 扩张极快，不是蘑菇云慢涨
- [ ] 全程不出现 MV 式慢镜头
