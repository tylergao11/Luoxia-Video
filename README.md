<!-- Banner -->
<div align="center">
  <img src="docs/images/LumenX-Studio-Banner-cybr.png" alt="Luoxia-Video" width="100%" />
</div>

<div align="center">

# Luoxia-Video

### 落霞 · 小说转短剧创作平台
**Novel → Beats → Timeline → Short Drama**

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/)

[English](README_EN.md) · [中文](README.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

</div>

---

**Luoxia-Video（落霞）** 是一套 **AI 原生的小说转短剧** 全链路产品。调度真源是落霞契约：**beats** 管内容取舍，**timeline** 管 audio-first 时长；应用壳提供剧本、拆分审阅、定妆/资产、分镜出片与合成导出，以及独立 Playground。

| 模块 | 定位 |
|------|------|
| **Pipeline** | 小说/剧本 → 落霞 beats 拆分审阅 → 画风/角色 → 分镜视频 → 合成导出 |
| **Playground** | 独立图像/视频生成工具台（无需剧本上下文，即开即用） |
| **CLI（进阶）** | `python -m src.luoxia` 无人值守出片，与应用同一产品、同一契约 |

### 当前生产默认契约

| 环节 | 唯一默认 |
|------|----------|
| **语音** | 豆包 Seed-TTS 2.0；角色音色与语速等参数由 cast/audio 真相源管理 |
| **视频** | Grok `grok-imagine-video-1.5`；默认通过 Grok 登录/订阅池（`session` + `xai_pool`）鉴权 |
| **首帧** | 红果路线、国风精致 3D AI 漫剧、非真人写实、横屏 `16:9`；脸与服装服从角色母图 |
| **产物** | `output/` 对 Git 可见，作为公司/家庭工作站之间同步的生产真相 |

### Grok 视频生成与切片设计

- Grok 视频最多同时生成 **2 个任务**，互不依赖的镜头可以并行；每段视频最长 **15 秒**。
- 有中文对白时，以完整中文语音驱动口型，嘴型必须对应中文发音。语音优先，禁止截断最后一个字、尾音或自然收口。
- 只有用户明确要求“连贯一镜到底”时，才把上一段视频的最后一帧作为下一段视频的首帧，让超过单段上限的连续长镜头顺接下去。
- 普通分镜按完整的叙事信息和动作单元切片：一个必要动作完成就切，不把无意义的走动、转身、伸手、停留等残余动作莫名延续到下一镜。
- 切片点必须落在动作完成、信息揭示或情绪落点上，不能从人体动作的中间随意劈开，也不能为了凑时长添加重复反应或空动作。

### 角色一致性与脸母图

- 用户最终确认的脸母图是角色身份的唯一真相源；每个新镜头都从这张原始母图出发，禁止把生成结果继续当作下一轮脸部参考，避免误差逐轮累积。
- 脸母图只负责五官、脸型、年龄感与角色身份；居家、睡觉、外出等服装母图只负责对应服装，不得反向改变角色的脸。
- 全身图生成只负责身体、服装、姿势和构图，不得重新设计五官。需要严格一致时，保留母图脸部内部特征，只处理与发际线、下颌和脖子衔接有关的边缘。
- 中文对白优先使用能清楚看见说话者口部的中景或近景；全身远景不能作为脸部一致性与中文口型的主要验收画面。
- “连贯一镜到底”的上一段尾帧只负责同一连续镜头内部的画面顺接，不能代替原始脸母图成为后续新镜头或新场景的角色身份源。

---

## ✨ 核心能力

<table>
<tr>
<td width="50%">

### 🎬 Studio — 全链路漫剧生产

- **深度剧本分析** — LLM 自动提取角色/场景/道具，生成结构化分镜脚本
- **可控美术指导** — 自定义视觉风格，全片画风统一
- **多模型资产生成** — 角色三视图、场景定调图、道具参考图
- **AI 分镜视频** — I2V / R2V 多模式视频生成 + 批量抽卡
- **智能配音** — 豆包 Seed-TTS 2.0 多音色对白合成
- **一键合成导出** — 时间线编辑 + FFmpeg 拼接成片

</td>
<td width="50%">

### 🎨 Playground — 独立生成工具台

- **6 种生成模式** — 图像生成、文生视频、图生视频、参考生视频、视频编辑
- **10+ AI 模型** — GPT-Image-2、Wan 2.7、Seedance 2.0、Kling V3、Vidu Q3、HappyHorse 等
- **动态参数** — 每个模型独立参数（尺寸/分辨率/时长/画质）
- **并发任务** — 多任务同时执行，实时状态追踪
- **Prompt 模板** — 收藏/复用/历史记录
- **画廊视图** — 网格/画廊切换 + 详情面板

</td>
</tr>
</table>

---

## 🎨 v1.2.1 主视觉焕新

<div align="center">

| Before | After |
|:---:|:---:|
| <img src="docs/images/LumenX Studio Banner.jpeg" alt="旧版 Banner" width="100%" /> | <img src="docs/images/LumenX-Studio-Banner-cybr.png" alt="新版 Banner" width="100%" /> |
| 霓虹渐变莲花 · 柔和曲线 | Cyber Brutalism · 棱角几何 · 电路纹理 |

</div>

---

## 📸 产品截图

<div align="center">

| Studio 分镜工作台 | Playground 创作台 |
|:---:|:---:|
| <img src="docs/images/studio-storyboard.jpg" alt="Studio" width="100%" /> | <img src="docs/images/playground-overview.jpg" alt="Playground" width="100%" /> |

</div>

---

## 🎯 支持的 AI 模型

| Provider | 模型 | 能力 |
|----------|------|------|
| **DashScope** | Wan 2.7 Image/Video, Qwen Image 2.0, HappyHorse 1.0 | T2I, I2I, I2V, R2V, T2V, V2V |
| **DashScope** | Kling V3 | I2V, R2V |
| **DashScope** | Vidu Q3 Pro / Turbo | I2V, R2V |
| **DashScope** | PixVerse V6 / C1 | I2V, R2V |
| **MuleRun** | Seedance 2.0 | T2V, I2V, R2V |
| **MuleRun** | GPT-Image-2 | T2I, I2I (含 4K) |
| **Kling 原厂** | Kling V3 | I2V, R2V |
| **Vidu 原厂** | Vidu Q3 Pro / Turbo | I2V, R2V |
| **Grok 订阅池** | grok-imagine-video-1.5 | 主链视频生成 |
| **火山引擎 / 豆包** | Seed-TTS 2.0 | 主链 TTS 配音 |
| **DashScope** | Qwen 3.7 Plus | 剧本分析、Prompt 润色 |

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- FFmpeg（视频处理）

### 一键启动

```bash
# 克隆
git clone https://github.com/alibaba/lumenx.git
cd lumenx

# 配置生产主链
cp .env.example .env
# 编辑 .env，填入 VOLCENGINE_TTS_API_KEY，并在应用中完成 Grok 登录
# 只有显式使用 DashScope 模型时才需要 DASHSCOPE_API_KEY

# 启动（后端 17177 + 前端 3008，自动开浏览器）
npm run dev
```

或分别启动：

```bash
# 后端
pip install -r requirements.txt
./start_backend.sh  # http://localhost:17177

# 前端
cd frontend && npm install && npm run dev  # http://localhost:3008
```

### 访问

- **Studio**: http://localhost:3008
- **Playground 创作台**: http://localhost:3008/#/playground
- **API Docs**: http://localhost:17177/docs

---

## ⚙️ 配置模式

Luoxia-Video 采用 **本地优先** 的架构；生产主链使用 Grok 登录/订阅池生成视频，使用豆包 API Key 生成语音。

| 模式 | 必填 | 可用能力 |
|------|------|----------|
| **落霞主链** | Grok 登录/订阅池 + `VOLCENGINE_TTS_API_KEY` | 红果国风 3D AI 漫剧首帧 + Grok 视频 + 豆包配音 |
| **+ DashScope** | + `DASHSCOPE_API_KEY` | Wan/Qwen/HappyHorse/PixVerse/Kling(代理)/Vidu(代理) |
| **+ MuleRun** | + `mulerun login` 或 `MULEROUTER_API_KEY` | + Seedance 2.0 + GPT-Image-2 |
| **+ Kling 原厂** | + `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | Kling 直连 |
| **+ Vidu 原厂** | + `VIDU_API_KEY` | Vidu 直连 |
| **+ OSS** | + 阿里云 OSS 凭证 | 云端媒体镜像 + 签名 URL |

<details>
<summary>详细配置说明</summary>

所有配置可通过以下方式设置：
- **开发模式**: 项目根目录 `.env` 文件
- **应用内设置**: Settings 页面（保存到 `~/.lumen-x/config.json`）

MuleRun 支持两种认证方式：
1. **CLI 模式**（推荐）: `npm i -g @mulerunai/cli && mulerun login`
2. **API Key 模式**: 在设置页填入 `muk-...` 格式的 Key

</details>

---

## 🏗️ 技术架构

<div align="center">
  <img src="docs/images/architecture-cybr.png" alt="Luoxia-Video System Architecture" width="90%" />
</div>

### 目录结构

```
lumenx/
├── frontend/                  # Next.js 前端
│   └── src/components/
│       ├── modules/playground/   # Playground 创作台
│       ├── modules/              # Studio 业务模块
│       └── layout/               # 全局布局
├── src/
│   ├── apps/comic_gen/        # Studio 后端 (API + Pipeline)
│   ├── apps/playground/       # Playground 后端 (API + Service)
│   ├── models/                # AI 模型适配器 (Wanx/Kling/Vidu/MuleRouter)
│   └── audio/                 # TTS 语音合成
├── config/model_catalog/      # 模型目录 (YAML → JSON)
└── output/                    # 生产产物真相（由 Git 跨工作站同步）
```

---

## 📖 文档

| 文档 | 说明 |
|------|------|
| [用户手册](USER_MANUAL.md) | 功能使用说明 |
| [API 文档](http://localhost:17177/docs) | Swagger UI |
| [模型接入](docs/model-onboarding-implementation.md) | 新模型接入指南 |
| [Catalog 架构](docs/plans/2026-04-03-model-docs-and-catalog-architecture.md) | 模型目录设计 |
| [Playground PRD](docs/plans/2026-06-06-playground-standalone-generation-prd.md) | 创作台设计文档 |

---

## 🤝 参与贡献

欢迎社区贡献！请先阅读 [贡献指南](CONTRIBUTING.md)。

- **Bug 反馈**: [GitHub Issues](https://github.com/alibaba/lumenx/issues)
- **功能建议**: [GitHub Discussions](https://github.com/alibaba/lumenx/discussions)
- **邮件联系**: [zhangjunhe.zjh@alibaba-inc.com](mailto:zhangjunhe.zjh@alibaba-inc.com)

---

## 📄 License

[MIT License](LICENSE)

---

<div align="center">
  Made with ❤️ by StarLotus · Alibaba Group
</div>
