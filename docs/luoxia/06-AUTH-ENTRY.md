# 06 · 入口鉴权（可切换订阅池）

鉴权是 **入口处配置**，不是 beats/timeline 业务的一部分。

## 模式 `LUOXIA_AUTH_MODE`

| mode | 含义 |
|------|------|
| `session`（默认） | 订阅池 / OAuth 会话；缺会话 → **Need login**，不是缺 API Key |
| `api_key` | 环境变量长效 Key（按量备用） |
| `offline` | 不走云（still-hold / 本地） |

## 供应商 `LUOXIA_AUTH_PROVIDER`

默认 `xai_pool`。换池 = **新适配器 + 改 provider id**，不改 pipeline。

已注册：

- `xai_pool` — **Grok 登录**（复用 Grok CLI 会话）或粘贴 access_token
- `api_key_bundle` — 直连 env keys
- `offline` — 无云

扩展：在 `src/auth/providers/` 加类，于 `registry.ensure_builtin_providers` 注册。

## API

- `GET /auth/status` `GET|PUT /auth/config`
- `POST /auth/login` `{ "action": "grok_login" }` 或 `{ "action": "token", "access_token": "..." }`
- `POST /auth/logout`

UI：设置 → API Keys 顶部「登录 / 订阅池」→ **Grok 登录**。
