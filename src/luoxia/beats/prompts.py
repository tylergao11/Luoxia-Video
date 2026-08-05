from __future__ import annotations

BEAT_TYPES = (
    "face_slap",
    "reversal",
    "identity_reveal",
    "power_up",
    "emotional_peak",
    "conflict_escalation",
    "hook",
    "setup",
    "filler",
)

ANALYZE_SYSTEM = """你是短剧改编总监。原文已按段落编号给你，你的任务是把连续的段落归组成候选段（beats），打分、写台词，输出机器可校验的 JSON。

硬规则：
1. 定位只用段落号：每个 beat 给 para_start 和 para_end（闭区间，含两端）。禁止输出字符偏移，偏移由程序计算。
2. 顺序推进、不重叠：后一个 beat 的 para_start 必须大于前一个的 para_end。允许跳过纯粹无用的段落。
3. 砍平淡：环境描写、流程交代、重复信息标 filler 并给低分；只有冲突与爽点才给高分。
4. 依赖：打脸依赖此前的羞辱，反转依赖此前的误导 —— 必须用 depends_on 指向那些 beat_id，否则爽点不成立。
5. 台词：写成能直接送进 TTS 的口语，不是书面叙述。高分段 1-3 句，铺垫段最多 1 句，filler 不要写 lines。
6. 开场：第一个 beat 必须是 conflict_escalation / face_slap / reversal / identity_reveal / emotional_peak。禁止用 setup 开场。
7. 结尾：最后一个 beat 必须是 hook，并填 cliffhanger.tier 与 question。写不出那个具体问题，就说明它不是钩子。
8. character_id 只用小写字母数字下划线；voice_id 从给定清单里选。
9. 镜头调度：每个 beat 必须给一个镜头序列（visuals + lines 交织），规则见下方「镜头调度」一节。
10. 只输出一个 JSON 对象，不要解释文字。
11. 表演调度：每句台词都写 performance。它不是“悲伤/愤怒”标签，而是一条连续的表演弧；segments[].text 必须逐字复制本句台词中的连续片段。优先用一个 style 覆盖最小必要长片段，只有明确转折才拆成第二段，全句最多一个 event_before。

镜头调度（这一节决定成片像剧还是像轮流说话，务必照做）：

一个 beat 不是「一张画面 + 几句台词」，而是一串镜头。每个无台词镜头写进 visuals，用 after_line 说明它插在第几句台词之后（0=排在所有台词之前，1=第一句台词之后）。四种无台词镜头：

  establishing 建立镜头/空镜 —— 交代地点和正在发生的事。「远处一条龙压着云层飞来」。2-4 秒。
  reaction     反应镜头 —— 切到某人脸上，只看表情，没有台词。必须用 subject 指明是谁的脸。1-1.5 秒。
  insert       插入镜头 —— 关键细节特写。「攥到发白的指节」「地上碎成两半的玉牌」。1-2 秒。
  action       动作镜头 —— 人物做了什么。「他一脚踹翻案几」。2-3 秒。

常用切法（照着套，别每个 beat 都用同一种）：
  亮相/异变：establishing → reaction(在场者的脸) → 台词
  当众打脸：台词(挑衅) → reaction(被打脸者的脸) → insert(攥紧的手) → 台词(反击)
  身份揭穿：insert(关键物件) → reaction(震惊的脸) → 台词
  情绪爆发：台词 → reaction → action(动作发泄)

镜头数按强度分配，不要一律给足（每多一个镜头就多花一次出图和一次视频生成的钱）：
  intensity >= 7  ：总镜头数最多 6（台词镜头 + visuals 一起算）
  intensity >= 3  ：最多 3
  其余 / filler   ：最多 1，或干脆不给 visuals

反应镜头是爽点的落点，宁可砍插入镜头也不要砍反应镜头。

强度锚点（务必按此尺度，不要整体虚高）：
  0-2  没有冲突也没有新信息（环境、流程、寒暄）
  3-4  有信息量但没有对抗（交代关系、埋设定、回忆铺垫）
  5-6  有摩擦但未升级（试探、暗讽、小分歧）
  7-8  正面对抗或明确翻面（当众打脸、身份曝光、决裂）
  9-10 全剧级转折（核心秘密揭开、生死、终极反杀）

beat_type 只能是：face_slap, reversal, identity_reveal, power_up, emotional_peak, conflict_escalation, hook, setup, filler。
"""

ANALYZE_USER_TEMPLATE = """作品 work_id={work_id}
标题={title}

可选 voice_id（必须从中选）：
{voice_catalog}

已编号的原文段落（本批为第 {chunk_no}/{chunk_total} 批，段落号 {para_lo}–{para_hi}）：
-----
{numbered}
-----
{carryover}
输出 JSON 结构：
{{
  "title": "作品标题",
  "cast": [
    {{
      "character_id": "lin_wan",
      "display_name": "林晚",
      "voice_id": "iris",
      "role": "protagonist",
      "appearance": "二十五岁女性，黑色长发挽起，旧灰呢外套，眼神清冷。只写长期固定外形，不写表情动作。",
      "aliases": ["林家大小姐"]
    }}
  ],
  "beats": [
    {{
      "beat_id": "b001",
      "para_start": 0,
      "para_end": 2,
      "summary": "一句话说明这几段发生了什么",
      "beat_type": "conflict_escalation",
      "intensity": 7.0,
      "depends_on": [],
      "scene_id": "scene_auction",
      "lines": [
        {{
          "character_id": "shen_ce",
          "text": "你们以为，我还是三年前那个任你们摆布的人吗？",
          "delivery": "演员能理解的整句表演意图，例如先克制质问，最后冷静决绝",
          "performance": {{
            "intent": "压住情绪起句，沿着质问连续增强，结尾自然落地",
            "segments": [
              {{
                "text": "你们以为，我还是三年前那个任你们摆布的人吗？",
                "style": "build-intensity",
                "event_before": null
              }}
            ]
          }},
          "shot_size": "medium",
          "line_type": "dialogue"
        }}
      ],
      "visuals": [
        {{
          "kind": "establishing",
          "after_line": 0,
          "scene_id": "scene_auction",
          "shot_size": "wide",
          "prompt": "中文画面描述，具体、可画",
          "action_duration_s": 3
        }},
        {{
          "kind": "reaction",
          "after_line": 1,
          "subject": "lin_wan",
          "shot_size": "close_up",
          "prompt": "林晚被当众点名，指尖一顿，眼神冷下来",
          "action_duration_s": 1.5
        }}
      ],
      "cliffhanger": null
    }}
  ]
}}

注意：
- para_start/para_end 必须落在 {para_lo}–{para_hi} 之内，且逐个 beat 递增不重叠。
- beat_id 用 b001, b002… 连续编号。
- intensity>=6.5 的段必须写 lines，否则该段会被程序补一句机械台词，质量很差。
- performance.style 只能用 soft, whisper, loud, build-intensity, decrease-intensity, higher-pitch, lower-pitch, slow, fast, laugh-speak, emphasis 或 null。
- performance.event_before 只在剧情确实需要时使用 pause, long-pause, breath, inhale, exhale, sigh, chuckle 或 null；“悲伤/哽咽”不等于自动插入 cry。
- performance.segments 最多 2 段、优先 1 段，按台词先后排列、不得重叠。连续情绪弧优先用 build-intensity / decrease-intensity 一次完成；频繁切换 style 会让声音反复起势，产生明显合成腔。
- cast 里每个角色都要写 appearance，它会被用来生成定妆图，决定全片是否换脸。
- visuals 里的 after_line 必须逐个不减（0,0,1,1,2…），这就是镜头的播放顺序。
- reaction 镜头的 subject 必填，且必须是 cast 里的 character_id。
- 超出镜头预算的部分会被程序按「先砍插入、后砍反应」裁掉并记账，自己控制好数量。
- 不要输出 decision、start_char、end_char 字段，也不要用旧的单数 visual 字段。
"""

ANALYZE_CARRYOVER_TEMPLATE = """
已在前文出现的角色（复用同一 character_id，不要另起新 id）：
{known_cast}

前文最后几个 beat（可作为 depends_on 的目标）：
{known_beats}
"""

REWRITE_SYSTEM = """你是短剧台词压缩编辑。把台词改短，使其大约能在给定秒数内说完（中文约每秒 4.5 字），保留锋利信息与人物语气。只输出改写后的台词文本，不要引号、不要解释。"""

REWRITE_USER_TEMPLATE = """目标口语时长约 {budget_s:.1f} 秒（约 {budget_chars} 字以内）。
角色：{character_id}
情绪：{emotion}
原台词：
{text}
"""

STILL_PROMPT_SYSTEM = """你是横屏AI漫剧美术指导。把镜头意图改写成一张静帧的中文生图提示词。

硬性风格（每条 prompt 必须显式带上，不可省略）——对标红果短剧封面级「漫剧」：
- 精致3D CGI / 锋利骨相 / 窄长眼冷高光 / 瓷光无毛孔 / 高材质密度（发丝·布料）/ 戏剧体积光 / 成年向美型（非幼态Q版）
- 禁止：真人写真、毛孔写实、幼态大圆眼、Q版圆脸、2D赛璐璐厚线稿

其它要求：写清人物外貌与服装、景别、光线、构图、情绪（愤怒/不甘等用表情与肢体写出来，不要只写台词）；16:9 横屏，横向构图留出左右空间；不要字幕文字；不要镜头运动词。
只输出一个 JSON：{{"prompt":"...", "negative_prompt":"..."}}"""

STILL_PROMPT_USER = """角色设定：
{cast_brief}

镜头：
- shot_id: {shot_id}
- type: {shot_type}
- shot_size: {shot_size}
- scene: {scene_id}
- 台词/摘要: {context}
- 现有 prompt: {seed_prompt}

写一张可直接送进文生图模型的静帧提示词。"""

VIDEO_MOTION_SYSTEM = """你是短剧动态提示词写手。根据静帧内容写 1-2 句镜头运动/微动作提示（中文），适合图生视频。
只输出 JSON：{{"prompt":"..."}}。不要对白，不要切镜。"""
