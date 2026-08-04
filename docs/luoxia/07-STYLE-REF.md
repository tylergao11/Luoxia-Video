# 07 · 生图风格参考（红果 AI 漫 · 几乎一致配方）

## 问题

纯文生图，或把风格图当成「固定长相」，会落到：

- 偶像/毛孔写实路人，或  
- 幼态 Q 版大圆眼美少年  

都对不齐红果封面级 **精致 3D 漫剧**（锋利骨相、窄长冷眼、瓷光、高材质密度、戏剧体积光）。

## 采用方案

在现有 xAI `/images/edits` 上做 **风格 / 身份解耦**（IP-Adapter 式分工，文本声明 role）：

| role | 含义 | 指令要点 |
| --- | --- | --- |
| `style` | 介质 / 光妆 / 材质 / 脸模锋利度 | 与参考 **几乎同一套渲染语言**；禁止抄银发红瞳装/UI |
| `identity`（默认） | 角色锁脸 | 保持本片角色五官发型服装 |

实现：

- `src/models/xai_image.py` → `compose_prompt` / `_compose_ref_instructions`
- `src/luoxia/stills/characters.py` → `HONGGUO_STYLE_LOCK`、`style_ref_images`
- 定妆可走 **image edit 强迁移**：以干净风格帧为 `image`，提示「保持同等渲染，只改身份」

## 默认风格锁（产品）

正向关键词（`HONGGUO_STYLE_LOCK`）：

- 红果短剧 / 精致 3D CGI / 虚幻引擎级材质密度  
- **锋利骨相、修长脸、窄长眼、冷高光**  
- 瓷光无毛孔、发丝丝缕、布料纤维  
- 戏剧体积光、封面级精修、**成年向（非幼态 Q 版）**

负向必压：

- photoreal / live action / 毛孔写实  
- chibi / baby face / big round moe eyes / Q 版  
- silver hair / red eyes（防风格参考身份泄漏）  
- 2D 赛璐璐厚线稿

## 几乎一致 · 可复现配方

1. **风格参考裁剪**  
   - 源：`output/doupo_moyan/refs/style_ref_male_closeup.jpg`  
   - 裁掉 UI/字幕，保留脸+肩：`style_ref_male_face_tight.jpg`  
   - `role: "style"`

2. **定妆（近一致主路径）**  
   - 优先：edits 以 tight 风格帧为输入，提示：  
     `Keep EXACT same premium 3D CGI render quality… change identity to black messy hair, dark eyes, blue worn robe Xiao Yan… NO silver/white hair, NO red eyes…`  
   - 或：style-only ref + `HONGGUO_STYLE_LOCK` + 窄长眼/锋利骨相/瓷光/高材质 文案（勿写「大眼萌」）  
   - 产物：`output/doupo_moyan/characters/xiao_yan.png`

3. **分镜**  
   - `ref_images = [style, identity]`  
   - 场景文案重复：窄长冷眼、锋利骨相、瓷光、材质密度、戏剧体积光  
   - 产物：`output/doupo_moyan/stills/s03_declaration.png`

4. **验收轴（几乎一致 = 风格族近一致，非身份克隆）**  
   - 材质密度、锋利骨相/窄长眼气场、瓷光、戏剧光 ≈ 参考  
   - 不得出现银发/红瞳/魔尊装/UI 字幕  

## 踩坑

| 错 | 结果 |
| --- | --- |
| 风格图 +「固定长相」 | 学不会介质或抄错身份 |
| 只堆「大眼二次元」 | Q 版圆脸，离红果锋利封面更远 |
| 不裁 UI | 水印/备案字进图 |
| 要像素级复刻魔尊脸 | 越界；目标是介质几乎一致 |

## 验证样例（本轮）

对照 `refs/style_ref_male_closeup.jpg`：

- `characters/xiao_yan.png` — image-edit 强迁移定妆  
- `stills/s03_declaration.png` — style + identity 分镜  

## 用户已准星（2026-08）

用户确认下列定妆为 **对的** 红果 AI 漫完成度，后续分镜/锁脸以此为 identity + 风格锚：

- `output/doupo_moyan/characters/xiao_yan.png`  
- 金标副本：`output/doupo_moyan/refs/xiao_yan_style_gold.jpg`  

验收标准：新图应与这张 **同材质密度 / 同锋利骨相与窄长冷眼 / 同瓷光与戏剧光**，而不是退回 Q 版圆脸或真人写真。  
