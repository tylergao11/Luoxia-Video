# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Git Commit Rules

- Git author is already configured for this repo, do not modify git config
- **NEVER** add `Co-Authored-By` lines in commit messages
- Push to GitHub remote (`github`) only, ignore `origin` (deprecated GitLab)

## Project Workflow Triggers

When the user asks to do any of the following in this repository:

- publish to the Luoxia-Video GitHub mirror
- run the Luoxia-Video GitHub publish workflow
- follow the Luoxia-Video GitHub release or PR flow
- prepare a GitHub-safe branch, commit, push, or PR for Luoxia-Video
- use `/lumenx-git-publish` (legacy alias)

Treat that as a request to load and follow:

`.codex/workflows/lumenx-git-publish.md`

When the user asks to do any of the following in this repository:

- onboard a new model into Luoxia-Video
- update model docs, model versions, defaults, or parameters
- refresh Wan / Kling / Vidu / PixVerse model support
- run the Luoxia-Video model onboarding workflow
- review whether a model change is catalog-only or also needs runtime / UI work
- use `/lumenx-model-onboarding` (legacy alias)

Treat that as a request to load and follow:

`.codex/workflows/lumenx-model-onboarding.md`

When the user asks to do any of the following in this repository:

- build the Luoxia-Video desktop app
- package Luoxia-Video for macOS or Windows
- create a DMG or EXE build
- run the Luoxia-Video desktop build workflow
- use `/lumenx-build` (legacy alias)

Treat that as a request to load and follow:

`.codex/workflows/lumenx-build.md`

This repository does not rely on native slash commands in Codex. The strings `/lumenx-git-publish`, `/lumenx-build`, and `/lumenx-model-onboarding` are textual aliases for the workflows above (historical command filenames; product name is Luoxia-Video).

## Workflow Files

- `.claude/commands/lumenx-git-publish.md` remains the Claude project command source.
- `.claude/commands/lumenx-build.md` remains the Claude project command source.
- `.claude/commands/lumenx-model-onboarding.md` remains the Claude project command source.
- `.codex/workflows/lumenx-git-publish.md` is the Codex workflow mirror for the same project process.
- `.codex/workflows/lumenx-build.md` is the Codex workflow mirror for the desktop build process.
- `.codex/workflows/lumenx-model-onboarding.md` is the Codex workflow mirror for model onboarding, catalog updates, and verification.

If both Claude and Codex guidance exist, preserve behavior parity unless the user asks for divergence.

# Luoxia-Video Platform

## Overview

**Luoxia-Video** (落霞) is a novel-to-short-drama product. The short-drama spine is Luoxia contracts: **beats** own keep/drop content selection; **timeline** is the sole audio-first duration authority. The Next.js + FastAPI app is the human shell (script → beats review → cast/assets → storyboard → assembly). Do not brand this product as LumenX; upstream `alibaba/lumenx` is provenance only.

## Production Defaults and Truth Sources

- **语音**：主链固定使用豆包 Seed-TTS 2.0，唯一入口是 `src/luoxia/speech.py`，默认 `LUOXIA_TTS_PROVIDER=doubao`。音色、语速、音调与表演指令归 cast/audio 配置所有；Qwen3-TTS 与 xAI TTS 仅是显式兼容选项。
- **视频**：主链固定使用 Grok `grok-imagine-video-1.5`。默认鉴权是 `session` + `xai_pool`，即 Grok 登录/订阅池；只有用户明确选择 `api_key` 模式时才读取 `XAI_API_KEY`。
- **首帧**：固定为红果路线的国风精致 3D AI 漫剧，非真人写实，横屏 `16:9`。角色脸、年龄感、发型和服装以 cast 与身份/服装母图为真相源，不得套用固定的成年脸、男主脸或统一绿衣。
- **产物同步**：`output/` 是跨工作站同步的生产真相，必须对 Git 可见；不得在 `.gitignore` 中忽略 `output/` 或其子目录。

## Short-Drama Generation Guardrails

- 用户提供的成品提示词是该次生成的导演真相源。除替换已明确参数化的台词、首帧路径和实测音频时长外，不得擅自改写、扩写或加入 `slow`、`very slow`、`slow push-in`、`locked camera`、`subtle motion` 等导演指令。
- 有对白镜头必须先生成完整音频并实测时长；视频请求时长以实测音频为下限，合成必须保留完整话音和 timeline 的 `tail_out_s`，禁止在最后一个字、尾音或自然收口前硬切。
- 无对白 Grok 镜头若用户未指定时长，应让供应商使用默认成片节奏，返回后再记录实测时长；不得拍脑门填秒数，也不得为凑整集时长增加空镜、慢动作、重复反应或无意义首尾镜。
- 每个镜头必须新增叙事信息或完成一个必要动作，动作完成即切。纯观看、停留、握爪或空镜若不推进剧情，不得独立占用镜头。
- 说话者必须看向真实对话对象；自语角色看向场景内合理目标，不默认看镜头。首帧要同时保证视线轴成立和说话者口部可见。
- 人物动作必须符合当下动机。被嫌弃的小狐应留在原地或退缩，不能主动跟随；禁止为了画面动起来而添加与剧情相反的跳跃、行走或触碰。
- Grok 批量生成允许并行等待，但提交必须经过统一节流器，默认相邻请求至少间隔 2.1 秒；禁止同秒并发提交后再靠返工处理 429。
- 音色、`speech_rate`、`pitch_rate` 和 `voice_instructions` 属于 cast/audio 真相源，不得散落在临时生成脚本里，也不得把某一部剧的角色参数写成全局默认。

## Architecture

### Frontend
- Framework: Next.js 14 + React 18 + TypeScript + Tailwind CSS
- State management: Zustand
- HTTP client: Axios
- 3D rendering: Three.js + @react-three/fiber
- Animation: Framer Motion

### Backend
- Framework: FastAPI (Python 3.11+)
- AI integration: Alibaba Cloud Qwen/Wanx services via DashScope
- Data validation: Pydantic
- File storage: Local + Alibaba Cloud OSS

### Core Components

#### Frontend Structure
```
frontend/
├── src/app/              # Next.js App Router pages
├── src/components/       # React components
│   ├── layout/          # Layout components
│   ├── modules/         # Feature modules (ScriptInput, ArtDirection, etc.)
│   ├── canvas/          # Canvas-related components
│   └── project/         # Project-specific components
├── src/lib/             # Utilities (API client at api.ts)
└── src/store/           # Zustand stores
```

#### Backend Structure
```
src/
├── apps/comic_gen/      # Core comic generation logic
│   ├── api.py           # FastAPI routes (main entry point)
│   ├── pipeline.py      # Core business flow management
│   ├── models.py        # Data models (Pydantic)
│   ├── llm.py           # LLM interaction (script analysis, etc.)
│   ├── assets.py        # Asset generation (characters/scenes/props)
│   ├── storyboard.py    # Storyboard generation
│   ├── video.py         # Video generation
│   ├── audio.py         # Audio generation
│   └── export.py        # Video export/synthesis
├── models/              # AI model wrappers
├── utils/               # Utility functions (OSS integration)
└── config.py            # Global configuration
```

## Development Commands

### Initial Setup
```bash
# Copy environment template
cp .env.example .env
# Edit .env and add your Alibaba Cloud API keys
```

### Backend Development
```bash
# Install dependencies
pip install -r requirements.txt

# Create output directories
mkdir -p output/uploads

# Start backend server
./start_backend.sh
# or
python -m uvicorn src.apps.comic_gen.api:app --reload --host 0.0.0.0 --port 17177

# API docs available at: http://localhost:17177/docs
```

### Frontend Development
```bash
cd frontend
npm install
npm run dev
# Frontend available at: http://localhost:3008
```

### Full Development Mode
```bash
# Terminal 1: Start backend
./start_backend.sh

# Terminal 2: Start frontend
cd frontend && npm run dev
```

### Desktop App Mode
```bash
# Run the complete desktop application
python main.py
```

## File Structure

### Output Management
Generated files are stored in `output/`:
```
output/
├── assets/              # Character/scene/prop images
│   ├── characters/      # Character artwork
│   ├── scenes/          # Scene backgrounds
│   └── props/           # Prop items
├── storyboard/          # Storyboard renders
├── outputs/videos/      # Individual video segments
├── video/               # Final merged videos
├── uploads/             # User-uploaded files
└── video_inputs/        # Video generation source images
```

### Project Data
User project data is stored in `~/.tron/comic/`:
- `projects.json` - Main project database
- `app.log` - Application logs

## Key API Endpoints

### Project Management
- `POST /projects` - Create new project from script text
- `GET /projects` - List all projects
- `GET /projects/{id}` - Get project details
- `DELETE /projects/{id}` - Delete project
- `PUT /projects/{id}/reparse` - Reprocess script for project

### Asset Generation
- `POST /projects/{id}/generate_assets` - Generate all project assets
- `POST /projects/{id}/assets/generate` - Generate specific asset
- `POST /projects/{id}/assets/toggle_lock` - Lock/unlock asset
- `POST /projects/{id}/assets/update_image` - Update asset image

### Storyboard & Video
- `POST /projects/{id}/generate_storyboard` - Generate storyboards
- `POST /projects/{id}/storyboard/render` - Render specific frame
- `POST /projects/{id}/generate_video` - Generate videos from storyboards
- `POST /projects/{id}/video_tasks` - Create video generation tasks
- `POST /projects/{id}/merge` - Merge video segments

### Art Direction
- `POST /projects/{id}/art_direction/analyze` - Analyze script for style
- `POST /projects/{id}/art_direction/save` - Save art direction
- `GET /art_direction/presets` - Get style presets

## Development Guidelines

### Backend Changes
- Update Pydantic models in `src/apps/comic_gen/models.py` when modifying data structures
- Add new endpoints to `src/apps/comic_gen/api.py` using FastAPI conventions
- Implement business logic in appropriate modules in `pipeline.py`
- Use background tasks for AI processing operations

### Frontend Changes
- Add new API calls to `frontend/src/lib/api.ts`
- Create feature modules in `frontend/src/components/modules/`
- Use Zustand stores for shared state management
- Follow existing component structure patterns

### Configuration
- API keys can be configured via `.env` file or app settings dialog
- OSS configuration is optional but recommended for cloud storage
- Model settings can be changed per project via `update_model_settings`

## Debugging

### Common Issues
- FFmpeg not found: Install FFmpeg and ensure it's in PATH
- API keys missing: Configure via app settings or .env file
- OSS errors: Verify credentials and bucket permissions
- Video merge failures: Check if video files exist and have proper paths

### Logs
- Backend logs appear in terminal when running start_backend.sh
- Desktop app logs saved to: `~/.tron/comic/app.log`

## Deployment
- Frontend: Built with Next.js, can be deployed as static files
- Backend: Deploy with FastAPI server (Gunicorn recommended for production)
- Desktop app: Built with PyInstaller and pywebview

## Design Context

### Users
Primary: independent creators (self-media, short-video makers) who need to turn text scripts into comic-style videos quickly. Secondary: professional teams using it as a pre-production tool. Both share a need for speed and creative control — they think in stories, not in software.

### Brand Personality
**Creative · Immersive · Geeky** — Luoxia-Video feels like a creator's cockpit, not an admin panel. It respects the user's craft while putting AI power at their fingertips. Mission: novel in, short drama out.

### Aesthetic Direction
- **Dark-first**: Deep space black (#050508) background, no light mode. The darkness lets content (images, videos, storyboards) be the hero.
- **Glassmorphism**: Frosted glass panels (5% white + backdrop-blur) for structure. Layered transparency creates depth without clutter.
- **Neon accents**: Electric blue (#646cff) primary, hot pink (#ff0080) accent. Used sparingly for interactive elements and emphasis — not decoration.
- **Brand gradient**: Purple → Indigo → Pink. Reserved for branding moments, not sprinkled everywhere.
- **Typography**: Space Grotesk (display/headings — geometric, modern), Inter (body — clean, readable), JetBrains Mono (code/technical values).
- **Anti-references**: No dense tables/forms that feel like enterprise admin. No excessive particles/animations that distract from content. No multi-panel professional tool complexity (not Figma/Photoshop).

### Design Principles

1. **Content is king**: The user's creations (scripts, storyboards, videos, assets) should always be the visual focus. UI chrome stays quiet until needed.
2. **Progressive disclosure**: Show only what matters at each step. Advanced settings (prompt config, model settings) are accessible but not in-your-face. Use collapsible sections and contextual reveals.
3. **Confidence through feedback**: Every action should have clear, immediate visual feedback — loading states, success confirmations, smooth transitions. The user should always know what's happening and feel in control.
4. **Consistent glass language**: All containers use the glass-panel pattern. Inputs use glass-input. Buttons use glass-button or primary fills. No mixing of visual metaphors.
5. **Purposeful motion**: Framer Motion for meaningful transitions (enter/exit, state changes). Staggered reveals for lists. No gratuitous animation — every movement communicates something.
