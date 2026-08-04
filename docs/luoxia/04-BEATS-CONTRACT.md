# 04 · Beats 契约（爽点选取）

`contracts/beats.schema.json` · 实现在 `src/luoxia/beats/`

这是整条流水线的最上游。它回答一个问题：**几千字的原文，砍掉哪些、留下哪些、压到多短。**

产物是 `timeline.json` 的输入。分工很清楚——beats 决定内容取舍，timeline 决定时长排布。两者都不越界：beats 里不出现任何秒数决策，timeline 里不出现任何删改剧情的判断。

---

## 1. 为什么不直接让 LLM 写剧本

最省事的做法是把小说丢给模型，让它输出一份剧本。市面上的开源项目基本都这么干。它们的问题不在于写得不好，而在于**产物是一篇散文**：

- 你没法回答「第三集为什么这么平」——因为没有任何地方记录了哪些段落被砍、按什么标准砍的。
- 改一版提示词重跑，两份剧本没法 diff。变化是文字层面的，不是决策层面的。
- 砍掉铺垫、留下爽点这种硬伤，只有等成片出来观众看不懂了才发现。

所以这份契约把「选取」这件事从散文变成数据：每一段原文都有明确的去留决策、去留理由、以及它和其它段落的依赖关系。取舍是可以被机器检查的，也是可以被人一眼审计的。

改编质量本身仍然依赖模型的品味，契约管不了这个。但**因为漏掉铺垫导致爽点落空**这类结构性错误，契约能在生成任何一张图之前就拦下来。

---

## 1b. 定位只用段落号

`source_span` 是整份文档唯一的溯源锚点：它声称「这个 beat 来自原文第几到第几个字」。**这个字段绝不能由模型填。**

模型不会数字符。让它输出 `start_char` / `end_char`，它会给出看起来合理、实际错位的数字——而且 span 越界、重叠这些不变量照样能过，因为数字本身自洽。结果就是溯源链变成一篇编得很像的小说：`excerpt` 写着「这幅画的落款」，那段偏移下的原文其实是「水晶灯照着长廊」。校验全绿，审计全假。

所以协议改成：

1. 代码用 `src/luoxia/beats/segmenter.py` 把原文切成带编号的段落，每段的字符偏移由代码算，覆盖全文且互不重叠。
2. 模型只看到 `[0] …` `[1] …` 这样的编号列表，只返回 `para_start` / `para_end`。
3. 代码把段落号翻译回字符偏移，`excerpt` 一律从原文重新截取，模型给的 excerpt 直接丢弃。

由此得到三个白送的性质：不变量 3、4（span 有序、不重叠）**结构上不可能违反**；超长原文按段落分批，跨批偏移天然正确，不需要告诉模型「你的偏移要加 5500」；模型没归到任何 beat 的段落会被 `coverage_gaps` 找出来，补成 `filler` beat 并记账——否则被遗忘的段落既不算保留也不算丢弃，`drop_rate` 会悄悄失真。

---

## 1c. 修复账本

契约的价值是**在花钱之前知道模型干得差**。可一旦程序学会了自动兜底（高分段没台词就用摘要补一句、超预算就把句子砍一半、开场不够劲就改个类型），校验就永远是绿的——「模型干得差」被洗成了「校验通过」，契约反而帮着掩盖问题。

所以每一次程序替模型兜底，都必须写进 `repairs[]`：

| severity | 含义 | 例子 |
| --- | --- | --- |
| `low` | 只是记账，不影响叙事 | 自动分配音色、补 filler、去重段落 |
| `medium` | 结构被微调 | 冷开场重排、强行安上钩子、砍掉多余台词 |
| `high` | 内容被凭空生成或砍断 | 用摘要编出一句台词、没有分句点只能拦腰截断 |

`quality` 是它的汇总，花钱前只需要看一眼 `worst_severity`。要卡死就传 `--max-repair-severity medium`：一旦出现 high，`select_beats` 抛 `StrictRepairError`，pipeline 在生成任何定妆图和静帧之前停住，同时**仍然把 beats.json 写盘**，方便你打开看是哪几段被编的。

---

## 2. 阶段机

```
draft ──切片完成，未评分
  │
scored ──每段有类型和强度
  │
selected ──取舍落定，通过全部一致性校验，beats_hash 锁定
  │
delivered ──已生成 timeline 草稿
```

`selected` 之后再改任何决策，`beats_hash` 就对不上，下游会拒绝消费。想改就重跑 `beats-select`，让哈希重新生成——不允许悄悄改一句台词然后假装没动过。

各阶段的校验强度不同：`draft` 只查切片本身（ID、偏移、不重叠），`scored` 补查评分齐备，`selected` 才启用依赖、分集、压缩预算这些重头戏。

---

## 3. 爽点分类法

九类。前七类是观众真正要看的东西，`setup` 是让它们成立的必要交代，`filler` 是默认要砍的。

### face_slap · 打脸

之前轻视、羞辱、误判主角的人，被当众证伪。

成立条件是**羞辱在先、证伪在后，且两者共享同一批见证者**。私下的证明不叫打脸。这类段落的强度几乎完全取决于此前羞辱铺得够不够狠——所以它几乎总是要写 `depends_on`。

常见误判：把单纯的「主角赢了」标成打脸。没有先前的轻视，就只是 `power_up`。

### reversal · 反转

局势或认知突然翻面。观众以为 A，结果是 B。

成立条件是**此前必须有明确的误导**。误导那一段通常是 `setup`，砍掉它反转就变成了平铺直叙。

常见误判：把「事情有了新进展」标成反转。进展不等于翻面，那是 `conflict_escalation`。

### identity_reveal · 身份揭露

隐藏的身份、实力、血缘或关系被曝光。

它和 `reversal` 的区别在于揭露的是**关于人的事实**而非关于局势的判断。实操中两者常同时发生，选主导的那个。

### power_up · 逆袭

主角获得能力、资源、地位，或者以碾压姿态取胜。

单独出现时强度有限，观众对「变强」本身没那么敏感。它真正的价值是给后面的 `face_slap` 供弹药，所以常被后续段落 `depends_on`。

### emotional_peak · 情绪爆点

告白、决裂、痛哭、牺牲、久别重逢。

判断标准是**人物关系在这一段发生了不可逆的变化**。哭一场但关系没变，那是 `filler`。

### conflict_escalation · 冲突升级

矛盾从潜伏推向对抗，或者对抗再上一个量级。

这是短剧最主要的推进手段，也是开场首选。第一集第一段几乎一定属于这一类。

### hook · 悬念钩子

抛出一个未解的问题，本段不回答。

判断标准很硬：**写不出那个具体问题，就不是钩子**。所以 schema 里 `cliffhanger.question` 是要填的——填不出来说明这段只是话说了一半，不是悬念。

### setup · 铺垫

本身不爽，但后面的爽点靠它成立。

这是整个分类法里最容易出事的一类。它的强度天然偏低（3-4 分），按阈值本该压缩甚至丢弃，可一旦丢了，依赖它的爽点就落空。契约用 `depends_on` + 依赖修复来处理这件事，见第 6 节。

铺垫的正确处理方式几乎总是**压缩而不是保留**：四百字的回忆压成一句当面对峙的台词，信息量不减，时长砍掉九成。

### filler · 平淡

环境描写、流程交代、重复信息、与主线无关的日常。

默认丢弃。契约里有一条硬规则：**`filler` 永远不能是 `keep`**。即便打分虚高，selector 也会强制降到 `compress`。这是防止模型偷懒——把所有段落都标成保留，是最省事也最没用的做法。

---

## 4. 强度锚点

`intensity` 是 0-10 的浮点数。没有锚点的话，每次打分的尺度都会漂移，阈值就失去意义。

| 分段 | 判据 | 典型内容 |
| --- | --- | --- |
| 0-2 | 没有冲突，也没有新信息 | 环境描写、流程介绍、寒暄 |
| 3-4 | 有信息量，但没有对抗 | 交代人物关系、埋设定、回忆铺垫 |
| 5-6 | 有摩擦，但未升级为正面冲突 | 试探、暗讽、小分歧 |
| 7-8 | 正面对抗，或明确的翻面 | 当众打脸、身份曝光、决裂 |
| 9-10 | 全剧级转折 | 核心秘密揭开、主要人物生死、终极反杀 |

默认阈值 `keep_threshold=6.5` / `compress_threshold=3.0` 正好卡在这张表的分界上：7 分以上完整改编，3-6 分压成一两句，3 分以下不进成片。阈值放在 `global` 里而不是代码里，不同题材可以调——悬疑剧可以把 `keep_threshold` 压到 6.0，因为它的信息密度本身就该高一些。

---

## 5. 卡点分级

| 等级 | 承诺 | 用法 |
| --- | --- | --- |
| `tier_1` | 跨多集才回答的核心问题 | 首集集尾、season 收尾。一部剧不超过三四个，用滥了就不值钱 |
| `tier_2` | 下一集开头就回答 | 一个矛盾单元的收束点 |
| `tier_3` | 本集内部回答 | 中段防划走 |
| `daily` | 不承诺回答 | 一句意味深长的话、一个眼神，纯节奏调剂 |

硬规则两条，都由校验器执行：

- **每集最后一段必须有 `cliffhanger.tier`。** 没有钩子的结尾等于告诉观众可以走了。
- **第一集第一段的类型必须在 `opening_conflict_types` 里。** 默认允许冲突升级、打脸、反转、身份揭露、情绪爆点，就是不允许用 `setup` 开场。短剧前十秒必须见矛盾，用铺垫开场是最常见的死法。

---

## 6. 依赖关系

这是整份契约最有价值的部分。

`depends_on` 声明「这一段能成立，靠的是前面哪几段」。打脸依赖此前的羞辱，反转依赖此前的误导，逆袭的爽感依赖此前的憋屈。

三条约束：

1. **保留的段落，其依赖不能是丢弃状态**（不变量 9）。砍掉羞辱只留打脸，观众不知道在打谁的脸。
2. **依赖必须在叙事顺序上先播出**（不变量 20）。校验的是 `episodes[].beat_ids` 的实际顺序，不是原文顺序——这样倒叙、闪回、把高潮提前都是合法的，只要铺垫确实排在了前面。
3. **合并掉的信息必须有去处**（不变量 8）。`drop_reason=merged` 必须写 `merged_into`，而且目标不能自己也被砍了。信息可以搬家，不能凭空消失。

selector 会自动修复第一条：发现保留段落的依赖被判丢弃，就把它升级成 `compress` 而不是报错——因为这几乎总是正确的处理方式（铺垫本来就该压缩）。修复过程迭代到不动点，被救回的段落会记进审计流水。

唯一会硬失败的情况是人工用 `decision_locked` 把某段锁成丢弃、同时又有段落依赖它。这时候是真的矛盾，需要人来决定是解锁还是删依赖。

---

## 7. 压缩预算

两个数字，都在 `selected` 阶段强制：

- **`max_compression_ratio`（默认 0.15）**：台词总字数 ÷ 原文总字数的上限。这就是「几千字压成几百字」这句话的可执行形式。样例里 2400 字原文出了 96 字对白，比值 0.04。
- **`min_drop_rate`（默认 0.3）**：被丢弃段落的占比下限。防止模型什么都不砍。

这两个数字由校验器从 `beats[]` 重新算出来，再和 `selection` 块里写的数字比对。手填一个好看的数字是过不了的。

`script_char_count` 同理——由 `lines[].text` 重算，对不上就报错。

---

## 8. 不变量清单

`validate_beats()` 逐条检查，共 21 条。测试用 `mutate_for_invariant_violation()` 对每一条构造反例，确认它确实会被抓住。

| # | 约束 | 生效阶段 |
| --- | --- | --- |
| 1 | `beat_id` 唯一 | 全部 |
| 2 | `index` 连续且与数组顺序一致 | 全部 |
| 3 | `source_span` 首尾合法且不越界 | 全部 |
| 4 | 相邻 `source_span` 不重叠（允许 gap，那是被跳过的原文） | 全部 |
| 5 | 每段有 `beat_type` 和 `intensity` | scored+ |
| 6 | 每段有 `decision` | selected+ |
| 7 | 保留的段落必须有 `lines` 或 `visuals` | selected+ |
| 8 | 丢弃必须有 `drop_reason`；`merged` 必须有存活的 `merged_into` | selected+ |
| 9 | 保留段落的 `depends_on` 不得指向被丢弃的段落 | selected+ |
| 10 | `filler` 不得 `keep` | 全部 |
| 11 | 每集最后一段必须有 `cliffhanger.tier` | selected+ |
| 12 | 每集至少一段达到 `episode_min_peak` | selected+ |
| 13 | 第一集首段类型必须在 `opening_conflict_types` 内 | selected+ |
| 14 | 压缩比不超过 `max_compression_ratio` | selected+ |
| 15 | 丢弃率不低于 `min_drop_rate` | selected+ |
| 16 | `script_char_count` 等于 `lines[].text` 长度之和 | 全部 |
| 17 | 台词的 `character_id` 必须在 `cast` 里 | 全部 |
| 18 | 至少一集；保留段落恰好归属一集，丢弃段落不得排进任何一集 | selected+ |
| 19 | `beats_hash` / `selected_at` 齐备，且 `selection` 统计与重算一致 | selected+ |
| 20 | `depends_on` 必须在叙事顺序上先于本段 | selected+ |
| 21 | 镜头调度合法：`after_line` 不越界且不倒退；`reaction` 必须指明 `subject`；`subject` 在 `cast` 里；总镜头数不超预算 | 全部 / 预算 selected+ |

---

## 8b. 镜头调度（coverage）

一个 beat 不是「一张画面 + 几句台词」。人看着像剧的东西，是镜头在切：

> 远处一条龙压着云层飞来 → 切到少年的脸，他瞳孔一缩 → 他说了句话 → 插入他攥紧的剑柄 → 切到对面那人，眉毛一挑 → 那人回话

第一版契约表达不了这个。`beat.visual` 是**单个**对象，桥接时固定插在该段所有台词之前，于是每个 beat 的镜头序列被硬编码成 `[一个空镜] + [每句台词一个镜头]`——成片只能是轮流说话。

1.1.0 改成 `beat.visuals[]`：**有序**的无台词镜头列表，用 `after_line` 插进台词序列（`0`=所有台词之前，`k`=第 k 句台词之后）。`lines[]` 仍是台词的唯一真源，所以压缩比、字数统计、`beats_hash` 全都不受影响。

| kind | 作用 | 建议时长 |
| --- | --- | --- |
| `establishing` | 建立镜头/空镜，交代地点与正在发生的事 | 2-4s |
| `reaction` | 切到某人脸上，只看表情，无台词。必须写 `subject` | 1-1.5s |
| `insert` | 关键细节特写（攥白的指节、碎掉的玉牌） | 1-2s |
| `action` | 人物做了什么 | 2-3s |

`reaction` 强制要求 `subject`，因为不指定是谁的脸，这个镜头既没法出图也没法用定妆图锁脸。桥接时反应镜头的 `characters` **只**放 `subject` 一个人——反应镜头里出现两个人就不是反应镜头了。反应/插入镜头的默认时长是 1.5 秒而不是 `default_action_duration_s`（4 秒），4 秒的反应镜头会把节奏彻底拖垮。

### 镜头预算：覆盖是要花钱的

每多一个镜头 = 多一次静帧生成 + 多一次视频生成。所以 `global.coverage` 按强度分配总镜头数（台词镜头 + `visuals` 一起算）：

| 强度 | 上限 | 够拍什么 |
| --- | --- | --- |
| `>= peak_threshold`（默认 7.0） | 6 | 建立→反应→台词→插入→反打→台词 |
| `>= compress_threshold`（默认 3.0） | 3 | 建立→台词→反应 |
| 其余 | 1 | 一个镜头交代完就走 |

超预算不是报错，而是由 selector 裁掉并记入 `repairs`（`coverage_trimmed`，medium）。裁剪顺序是 `insert → action → establishing → reaction`：**反应镜头最后才砍**，因为打脸类短剧的爽点落点就是对方那张脸；插入镜头是装饰，建立镜头的信息通常能从下一个台词镜头的背景里读出来。

预算只约束我们**额外加的**无台词镜头：实际上限是 `max(预算, 台词数)`，所以一个台词多的段落不会因为内容取舍已经批准的台词而被判超支。

镜头调度**不参与 `beats_hash`**。哈希只覆盖 `{decision, lines, source_span}`，也就是"砍什么、留什么、说什么"。覆盖方式是手法层决策，重调不该让选片结果失效——和 timeline 里 `transition` / `subtitle_style` 不进 `timeline_hash` 是同一个道理。

---

## 9. 与 timeline 的接口

`build_timeline_draft(beats_doc, episode_id)` 把一集展开成镜头：

- `visuals[]` 按 `after_line` **交织**进台词序列，每个 → 一个 `timing_driver=rhythm` 的镜头，`shot_id` 后缀 `_v<slot><序号>` 标明它插在哪
- 每条 `line` → 一个 `timing_driver=audio` 的镜头，`delivery` 写进 `dialogue.emotion`
- `kind` 映射成 timeline 的 `shots[].type`（`reaction` / `insert` / `action`，`establishing` 在无台词段落记作 `transition`），这样人工审片时一眼能看出镜头调度
- 所有镜头默认 `transition.kind=cut`：正反打叠化会读成时间跳跃，反应镜头必须硬切
- `cast` 透传，缺 `voice_id` 直接报错（TTS 跑不了）
- 旧的单数 `visual` 仍然接受，视作一个 `after_line=0` 的 `establishing` 镜头，只为兼容 1.0.0 文件

**草稿刻意不带任何时长**：所有音频驱动镜头的 `timing` 都是空的，因为时长只能来自 TTS 实测。这一点有专门的测试守着（`test_draft_carries_no_durations_of_its_own`），防止有人图省事在 beats 里塞一个估算秒数。

对应地，`validate_timeline()` 对 `phase=draft` 走一档放宽的校验：跳过所有依赖已解算时长的不变量（1/2/3/5/6/7/8/11/15），只查结构性问题——镜头序号、角色是否在 `cast` 里、画幅是否和 `global` 一致、改写次数上限。这样草稿仍然是可校验的，拼错一个 `character_id` 会在生成任何一张图之前就被拦下，而不是刷一屏「缺少 start_s」。

正确的链路是：

```
beats-select → beats-bridge → solve → freeze → render → compose
                （草稿）      （合法 timeline）
```

`tests/luoxia/test_beats_bridge.py::test_beats_to_solve_to_valid_timeline` 跑完整条链路，确认桥接产物过 solver 之后能通过 timeline 的全部 16 条不变量。

---

## 10. 命令

```bash
# 校验
python -m src.luoxia beats-validate contracts/examples/beats.example.json

# 打分完成后落定取舍并分集（会原地改写文件）
python -m src.luoxia beats-select output/luoxia_demo/beats.json

# 不接受被编造的台词就加严格门；有 high 级修补时退出码 3
python -m src.luoxia beats-select output/luoxia_demo/beats.json --max-repair-severity medium

# 每个角色出一张定妆图，后续所有静帧以它为 I2I 参考
python -m src.luoxia sheets output/luoxia_demo/beats.json

# 展开某一集为 timeline 草稿
python -m src.luoxia beats-bridge output/luoxia_demo/beats.json ep01

# 之后就是既有流程
python -m src.luoxia solve ep01
```

---

## 11. 实现与边界

切片打分已实现：`python -m src.luoxia analyze novel.txt`（`src/luoxia/beats/analyzer.py`），一键出片见 `docs/luoxia/05-RUNBOOK.md`。

仍然清楚的边界：

- **打分是主观的。** 锚点表只能减少漂移，不能保证品味。契约保证的是一致性（依赖不断、平淡被砍、压缩到位），不是好看。
- **`CHARS_PER_SECOND = 4.5` 只用于分集打包时估长。** 它不参与任何成片决策，真实时长永远来自 timeline 阶段的 ffprobe 实测。
- **分集算法是贪心的。** 按估算时长累加，只在有卡点的段落处切，峰值不足的一集向前并入。够用，但不是最优分集。
- **超长原文按 ~5500 字分批调 LLM。** 分批以段落为单位，偏移不会错；每批会带上已知角色和最近几个 beat 作为上下文，减少同一个人被拆成两个 ID。但跨批的依赖推理仍然靠模型自觉，契约只能事后检查依赖是否成立，不能替它想出来。
- **跨集依赖只做了顺序检查。** 第五集的爽点依赖第一集的铺垫是合法的，但契约不检查间隔是否太远导致观众忘了。
- **`appearance` 决定脸能不能锁住。** 模型没写就只能记一条 `appearance_missing`，该角色不会有定妆图，它的镜头会一张一个样。这是目前最需要人工过一眼的字段。
