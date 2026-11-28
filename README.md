# AI Chat Site (Standalone)

一个独立可对外访问的网站（带注册/登录），后端对接 Gemini API，用于聊天。

## 功能（v2）

- 模型：Gemini 3.8 / 3.7 / 3.5 Flash、3.5 Flash-Lite、3.1 Pro、2.5 Pro/Flash；绘图 Nano Banana 2 / Pro（列表见 `ai_chat_site/model_catalog.py`）
- 流式输出、停止生成、重新生成、编辑后重发；思考深度（快速 / 标准 / 深度）与思考摘要
- 工具：联网搜索（附来源）、读取网页链接、代码执行
- 文件：拖拽 / 粘贴 / 选择上传；PDF、图片、音视频原生理解；Word / Excel / PPT / 代码文本提取；图片与 PDF 一键 OCR
- 绘图：文字生图、上传图片改图、多轮修改、画面比例
- 对话：人设 / 系统提示词、置顶、全文搜索、导出 Markdown、自动标题、跨对话记忆（gemini-embedding-001）
- 管理后台 `/admin`：用户、用量统计、邀请码（`ADMIN_USERNAMES` 配置管理员）
- 深色 / 浅色主题，移动端适配

## 本地开发

- `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- 新建 `.env.dev`（已在 .gitignore 中），至少包含 `AI_CHAT_SITE_SECRET_KEY`、`GEMINI_API_KEY`、`AI_CHAT_SITE_ALLOWED_HOSTS=localhost,127.0.0.1`、`FORCE_HTTPS=0`、`DATABASE_PATH=./data/dev.sqlite3`、`UPLOAD_DIR=./data/uploads`
- 启动：`scripts/dev_local.sh`，访问 http://localhost:49193
- 端到端测试（会真实调用 Gemini）：`set -a; source .env.dev; set +a; .venv/bin/python scripts/smoke_test.py`

## 快速启动（Docker Compose）

1) 进入目录：

- `cd standalone_ai_chat_site`

2) 复制并编辑环境变量：

- `cp .env.example .env`
- 重点填写：`AI_CHAT_SITE_SECRET_KEY`、`GEMINI_API_KEY`、`AI_CHAT_SITE_ALLOWED_HOSTS`
- 如果用 Cloudflare Tunnel：再填 `CLOUDFLARED_TOKEN`
- 只允许邀请码注册：需要先生成邀请码（见下方）
- 建议开启 Turnstile：填 `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY`

3) 启动：

- Docker Compose v2 插件：`docker compose up -d --build`
- 或老命令：`docker-compose up -d --build`
- 或一键脚本：`./prod_up.sh`

使用 Tunnel（可选）：

- `docker-compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d --build`

固定端口：`49193`（非默认端口，默认仅监听 `127.0.0.1`，建议通过 Cloudflare Tunnel / Nginx 反代到公网）

本地纯 HTTP 调试时：

- `.env` 里把 `FORCE_HTTPS=0`
- `AI_CHAT_SITE_ALLOWED_HOSTS` 加上 `localhost,127.0.0.1`

## Cloudflare 部署提示（重要）

- 如果你打算 **开橙云代理（HTTP/HTTPS 反代）**，Cloudflare 只支持一组固定端口；“随机端口”很可能不在支持列表里。
  - 解决方案：改用 **DNS only（灰云）**，或使用 **Cloudflare Tunnel（cloudflared）** 把公网域名映射到任意本地端口。
- 无论哪种方式，建议在源站防火墙只允许 Cloudflare 出口 IP（或仅允许 Tunnel），避免绕过 CF 直连源站导致 `X-Forwarded-For` 被伪造。
- Nginx 反代示例：`standalone_ai_chat_site/nginx/ai_chat_site.conf.example`

### 复用你正式机现有的 Tunnel（推荐）

如果正式机已经在跑 `cloudflared` 容器，不需要再起一个新的 tunnel 容器，只需要把新域名指到本服务：

1) 先把本服务拉起（只监听本机）：

- `cd standalone_ai_chat_site && docker-compose up -d --build`

2) 生成邀请码（只拥有邀请码的人才可注册）：

- `docker exec -it ai_chat_site python -m ai_chat_site.invite_tool --count 5`

3) 找到 cloudflared 容器与运行方式：

- `docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Command}}' | grep -i cloudflared`
- `docker inspect -f '{{.HostConfig.NetworkMode}}' <cloudflared容器名>`
- `docker inspect <cloudflared容器名> | grep -E 'NetworkMode|Mounts|--token|config.yml' -n`

4) 按 cloudflared 网络模式选一种后端地址：

- 如果 cloudflared 是 `network_mode: host`：后端填 `http://127.0.0.1:49193`
- 如果 cloudflared 不是 host 网络：让 cloudflared 和 `ai_chat_site` 在同一个 Docker network，然后后端填 `http://ai-chat-site:49193`
  - 在正式机执行一次：`docker network create cf_tunnel || true`
  - 把现有 cloudflared 容器接入：`docker network connect cf_tunnel <cloudflared容器名> || true`
  - 启动本服务时加上网络 override：`docker-compose -f docker-compose.yml -f docker-compose.cf_tunnel_network.yml up -d --build`

5) 在 Cloudflare Zero Trust 控制台给这个 Tunnel 增加一个 Public Hostname（或修改 config.yml 的 ingress），hostname 指到你的新域名，service 指到上面选的后端地址。

### Turnstile（强烈建议）

开启 Cloudflare Turnstile 后，登录/注册会要求人机校验，可显著降低爆破/机器人注册风险：

- `.env` 里配置：`TURNSTILE_SITE_KEY`、`TURNSTILE_SECRET_KEY`

## 安全策略（已内置）

- 注册/登录/聊天接口都加了限流（Flask-Limiter）
- 表单 + JSON API 都开启 CSRF（Flask-WTF CSRFProtect）
- 强制安全 Cookie、Host 校验（`AI_CHAT_SITE_ALLOWED_HOSTS`）
- 默认启用一组安全响应头（CSP/HSTS/XFO 等）
