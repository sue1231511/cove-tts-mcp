# cove-tts-mcp

把 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 语音合成包成一个远程 MCP 服务。AI 客户端调用 `cove_tts` 工具，传一段文字，拿回一个可以直接播放、下载的音频链接。

- 一个 Docker 镜像，容器里两个进程：`api_v2.py`（GPT-SoVITS 推理，9880，只在容器内）和 `mcp_server.py`（MCP 服务，**9881，对外**）
- MCP 走 streamable HTTP，端点是 `/mcp/`
- 默认模型是 HuggingFace 上公开的 [OmniDimen/Gove](https://huggingface.co/OmniDimen/Gove)（GPT-SoVITS v2ProPlus 微调），构建镜像时自动下载
- **仓库不带参考音频**，部署时你自己提供一段（见下文）
- CPU 推理，不需要显卡

---

## 一、先准备参考音频

GPT-SoVITS 每次合成都要一段"参考音频"加上这段音频里说的话，用来定语气和音色。你需要准备：

- **一段 3 到 10 秒的音频**，wav 最稳妥。单人、干净、没有背景音乐和杂音，一句完整的话。超出 3~10 秒会直接启动失败。
- **这段音频里说的那句话**，一字不差，填进 `REF_PROMPT_TEXT`。
- **语言**，`zh` 或 `en`，填进 `REF_PROMPT_LANG`。

声音主要由模型权重决定，参考音频影响语气、节奏和一部分音色。请只用你有权使用的声音。

参考音频放到容器里，三种方式任选一种：

| 方式 | 怎么做 | 适合 |
|---|---|---|
| 下载地址 | 设置 `REF_AUDIO_URL`，容器启动时下载 | 云平台，不想改仓库 |
| 放进仓库 | fork 后把音频放在 `app/ref/ref.wav`，再构建 | 自己的私有 fork |
| 挂载 | 把文件挂载到容器的 `/app/ref/ref.wav`，或用 `REF_AUDIO_PATH` 指定别的路径 | 自己的服务器 |

`REF_AUDIO_URL` 必须是能直接下载到文件本身的地址（不是网盘的分享页面）。用了它以后每次容器重启都会重新下载一次。

---

## 二、环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `MCP_API_KEY` | 是 | 访问密钥，自己生成一个足够长的随机字符串（比如 `openssl rand -hex 32`）。没设置服务不会启动 |
| `REF_PROMPT_TEXT` | 是 | 参考音频里说的那句话 |
| `REF_PROMPT_LANG` | 否 | 参考音频的语言，`zh` / `en`，默认 `zh` |
| `REF_AUDIO_URL` | 三选一 | 参考音频下载地址 |
| `REF_AUDIO_PATH` | 否 | 参考音频在容器里的路径，默认 `/app/ref/ref.wav` |
| `PUBLIC_BASE_URL` | 建议 | 服务对外的地址，比如 `https://tts.example.com`。设置了工具返回完整链接，不设置只返回 `/audio/...` 这样的相对路径 |
| `AUDIO_TTL_SECONDS` | 否 | 生成的音频保留多久、链接多久有效，默认 `86400`（24 小时） |

启动时会先检查参考音频，任何一项不对容器直接退出，日志里 `[prepare-ref]` 开头的几行会写原因。

---

## 三、部署

任何能构建 Dockerfile、对外开放一个端口的地方都能跑：自己的服务器、云主机、各种容器平台。

### 需要知道的几件事

- **构建很慢**：要下载约 1.8GB 模型、安装 CPU 版 torch，第一次构建通常 10~20 分钟。只改代码重新构建时，模型那一层有缓存的话会快很多（取决于平台是否保留构建缓存）。
- **启动也要等**：容器起来后加载模型要 1~3 分钟，这段时间 `/health` 还连不上。
- **内存**：模型常驻内存，内存太小会在加载时被系统杀掉。建议预留 4GB 以上，以你机器上实际观察为准。
- **端口**：对外映射容器的 **9881**。
- **音频存在容器的 `/tmp`**：容器重启就没了，链接也随之失效。

### 用 Docker 跑

```bash
docker build -t cove-tts-mcp .

# 方式 A：启动时下载参考音频
docker run -d --name cove-tts-mcp --restart unless-stopped -p 9881:9881 \
  -e MCP_API_KEY=换成你的密钥 \
  -e REF_AUDIO_URL=https://example.com/my-ref.wav \
  -e REF_PROMPT_TEXT="参考音频里说的那句话" \
  -e REF_PROMPT_LANG=zh \
  -e PUBLIC_BASE_URL=http://你的IP或域名:9881 \
  cove-tts-mcp

# 方式 B：挂载本地文件
docker run -d --name cove-tts-mcp --restart unless-stopped -p 9881:9881 \
  -v /path/to/my-ref.wav:/app/ref/ref.wav:ro \
  -e MCP_API_KEY=换成你的密钥 \
  -e REF_PROMPT_TEXT="参考音频里说的那句话" \
  cove-tts-mcp

docker logs -f cove-tts-mcp     # 看到 [start] mcp pid=... 就是起来了
```

公网使用请放在 HTTPS 反向代理后面（Caddy、Nginx 等），客户端连 https 地址。

### 在容器平台上

各家界面不同，要做的事是一样的：

1. 从 GitHub 导入这个仓库（或你的 fork），让平台用根目录的 `Dockerfile` 构建
2. 填好上面的环境变量
3. 把容器端口 9881 暴露出去，拿到一个 https 域名
4. 把这个域名填进 `PUBLIC_BASE_URL`，重启一次

**我们自己是部署在 Zeabur 上的**，供参考：用 Git 方式添加服务、选这个仓库，在 Networking 里把 9881 绑定域名。服务在 Zeabur 的 HTTPS 代理后面，代码里已经处理了代理转发的 https 信息，不需要额外设置。

### 验证

浏览器打开 `https://你的域名/health`：

- `{"ok": true, "api_v2": "alive", "busy": false}`：正常
- 返回 503：推理进程挂了，看日志里 `[api_v2]` 开头的行
- 打不开：还在加载模型，或者端口没映射对

---

## 四、接到 AI 客户端

**地址写 `https://你的域名/mcp/`，结尾带斜杠。**

密钥三种带法，服务端都认，按客户端支持的选：

| 带法 | 写法 |
|---|---|
| 请求头 | `X-API-Key: 你的密钥` |
| 请求头 | `Authorization: Bearer 你的密钥` |
| 地址参数 | `https://你的域名/mcp/?key=你的密钥` |

能用请求头就用请求头。密钥放在地址里，会原样出现在服务器和平台的访问日志里。

支持 JSON 配置的客户端：

```json
{
  "mcpServers": {
    "cove-tts": {
      "type": "http",
      "url": "https://你的域名/mcp/",
      "headers": { "X-API-Key": "你的密钥" }
    }
  }
}
```

claude.ai 的自定义连接器：URL 填上面的地址，认证选不需要登录，能填请求头就填 `X-API-Key`；不能填就用 `?key=` 那种地址。

### 工具

`cove_tts(text, text_lang="zh")`

- `text`：要说的话，最多 500 字
- `text_lang`：`zh` 或 `en`
- 返回：合成时长、文件大小、一个签名链接 `/audio/{id}?exp=…&sig=…`

签名链接不含主密钥，只能访问这一个文件，`AUDIO_TTL_SECONDS` 之后失效，容器重启也会失效。链接可以放心发进聊天记录或群里。

---

## 五、常见问题

**连不上，日志里 `POST /mcp/` 是 401**
密钥没带上或者不对。日志里会有一行 `auth rejected ... has_x_api_key=... has_bearer=... has_query_key=...`，看是哪种都没带。

**客户端报 `.well-known/oauth-...` 相关错误**
服务不用 OAuth，这些地址固定返回 404。客户端里认证方式选"不需要登录"，用请求头或 `?key=` 带密钥。

**`Bad Request: No valid session ID provided`**
客户端手里的会话已经失效（最常见是服务重启过），服务端不认了。在客户端里把这个 MCP 断开重新连接即可。

**容器一启动就退出**
看 `[prepare-ref]` 开头的日志：没填 `REF_PROMPT_TEXT`、找不到参考音频、下载失败、时长不在 3~10 秒，都会写明。

**合成返回"排队超过 600 秒"**
同一时间只能合成一句，前面的任务太多或者太长。CPU 上一句话通常 10~30 秒。

**读错字**
叠字词和外文音译名是模型的弱项，换个说法。

---

## 六、已知限制

- 只支持中文和英文
- CPU 推理，一句话 10~30 秒，同一时间只合成一句，其余排队（最多等 600 秒）
- 音频存在容器 `/tmp`，重启即清空
- Gove 的 `.pth` 文件头是 `06`，这是 v2ProPlus 的版本标记，不是文件损坏

---

## 七、目录

```
├── Dockerfile            python:3.10-slim + CPU 版 torch
├── requirements.txt      推理需要的依赖（版本都锁了，改之前看注释）
├── download_models.sh    构建时下载模型并校验大小
├── prepare_ref.py        启动前下载、校验参考音频
├── start.sh              依次启动参考音频检查 → api_v2 → MCP，之后看守两个进程
├── mcp_server.py         MCP 服务：/mcp/、签名音频 /audio/{id}、/health、鉴权、排队、清理
└── app/
    ├── api_v2.py         GPT-SoVITS 原版推理接口
    ├── ref/              参考音频放这里（ref.wav），仓库里是空的
    ├── GPT_SoVITS/       GPT-SoVITS 源码（裁掉了训练相关）
    └── tools/            只保留推理用到的部分
```

## 许可与致谢

本仓库按 [MIT 协议](LICENSE) 发布。推理代码来自 GPT-SoVITS（MIT），模型在构建时从各发布方下载，各自遵循原许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
