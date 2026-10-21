# -*- coding: utf-8 -*-
"""cove-tts-mcp: GPT-SoVITS 语音合成的 MCP 服务。

对外暴露 streamable HTTP MCP 端点 (:9881/mcp)，内部转发给 GPT-SoVITS api_v2 (:9880)。
工具返回可直接下载/试听的音频 URL，不把大段 base64 塞进对话上下文。

参考音频:
- 仓库不附带参考音频。REF_AUDIO_PATH / REF_PROMPT_TEXT / REF_PROMPT_LANG 由部署者提供，
  start.sh 先跑 prepare_ref.py 下载和校验，这里只再确认一次文件在。

鉴权:
- /mcp: 环境变量 MCP_API_KEY 必须设置；客户端带 header `X-API-Key`、`Authorization: Bearer`
  或 query `?key=` 三者之一。
- /audio/{id}: 不认 MCP_API_KEY，只认每个文件单独签发的 `exp` + `sig`
  （HMAC-SHA256，密钥派生自 MCP_API_KEY）。音频链接会进对话记录、群消息和日志，
  所以链接里绝不能带主密钥；签名链接泄露了也只能听这一个文件，到期即失效。
- /health: 免鉴权。

并发与资源:
- api_v2 的 /tts 处理函数是 async 里直接跑阻塞推理，同一时刻只能处理一个请求，
  所以这里用信号量把合成串行化，排队等待而不是同时把请求堆给 api_v2。
- 合成在线程里跑（asyncio.to_thread），不阻塞本进程事件循环，合成期间
  /health、/audio 下载、MCP 握手照常响应。
- 生成的 wav 超过 AUDIO_TTL_SECONDS 自动删除（每次合成前清理一次），避免 /tmp 只进不出。
"""
import asyncio
import hashlib
import hmac
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cove-tts")

API = "http://127.0.0.1:9880/tts"
OUT_DIR = "/tmp/tts_out"
MAX_TEXT_CHARS = 500
TIMEOUT = 300
QUEUE_WAIT_TIMEOUT = 600  # 排队等前一个合成结束最多等多久（秒）

API_KEY = os.environ.get("MCP_API_KEY", "")
if not API_KEY:
    log.error("MCP_API_KEY is not set, refusing to start")
    sys.exit(1)

# 参考音频：取值规则和 prepare_ref.py 一致（那边已经下载、校验过）
REF_AUDIO = os.environ.get("REF_AUDIO_PATH", "").strip() or "/app/ref/ref.wav"
PROMPT_TEXT = os.environ.get("REF_PROMPT_TEXT", "").strip()
PROMPT_LANG = (os.environ.get("REF_PROMPT_LANG", "").strip() or "zh").lower()
if not PROMPT_TEXT:
    log.error("REF_PROMPT_TEXT is not set, refusing to start")
    sys.exit(1)
if PROMPT_LANG not in ("zh", "en"):
    log.error("REF_PROMPT_LANG=%r is not supported (zh / en), refusing to start", PROMPT_LANG)
    sys.exit(1)
if not os.path.isfile(REF_AUDIO):
    log.error("reference audio not found at %s, refusing to start", REF_AUDIO)
    sys.exit(1)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.error("env %s=%r is not an integer, using default %d", name, raw, default, exc_info=True)
        return default
    if value <= 0:
        log.error("env %s=%d must be positive, using default %d", name, value, default)
        return default
    return value


AUDIO_TTL_SECONDS = _env_int("AUDIO_TTL_SECONDS", 86400)

# 音频签名密钥从主密钥派生，和主密钥本身不同：即使签名算法被逆推，也拿不到 MCP_API_KEY。
_SIGN_KEY = hashlib.sha256(b"cove-tts-audio-sign:" + API_KEY.encode("utf-8")).digest()

try:
    API_V2_PID = int(os.environ.get("API_V2_PID", "0"))
except ValueError:
    log.error("env API_V2_PID=%r is not an integer, health check cannot track api_v2",
              os.environ.get("API_V2_PID"), exc_info=True)
    API_V2_PID = 0

os.makedirs(OUT_DIR, exist_ok=True)

mcp = FastMCP("cove-tts", host="0.0.0.0", port=9881)

_synth_lock = asyncio.Semaphore(1)
_busy = False


# ---------------------------------------------------------------- 签名 / 鉴权

def _sign(fid: str, exp: int) -> str:
    msg = f"{fid}.{exp}".encode("ascii")
    return hmac.new(_SIGN_KEY, msg, hashlib.sha256).hexdigest()


def _verify_audio_sig(fid: str, exp_raw: str, sig: str) -> bool:
    try:
        exp = int(exp_raw)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(_sign(fid, exp), sig or "")


def _key_ok(key: str) -> bool:
    return hmac.compare_digest((key or "").encode("utf-8"), API_KEY.encode("utf-8"))


# ---------------------------------------------------------------- 健康检查

def _api_v2_alive() -> bool:
    """api_v2 是否还活着。看 /proc/<pid>/stat 的状态字段：进程退出后在被 start.sh
    回收前是僵尸（Z），os.kill(pid, 0) 对僵尸照样成功，会误判为存活。"""
    if API_V2_PID <= 0:
        return False
    try:
        with open(f"/proc/{API_V2_PID}/stat", "r") as f:
            stat = f.read()
    except FileNotFoundError:
        return False
    except OSError:
        log.error("health: cannot read /proc/%s/stat", API_V2_PID, exc_info=True)
        return False
    # 格式: pid (comm) state ...；comm 可能含空格，所以从最后一个 ')' 之后取状态
    state = stat.rsplit(")", 1)[-1].split()[0:1]
    return bool(state) and state[0] != "Z"


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    # 不通过 HTTP 探测 api_v2：它合成期间整个事件循环被阻塞，探测必然超时，
    # 会把"正在干活"误报成"挂了"。这里只看进程是否还在，再附带是否正在合成。
    alive = _api_v2_alive()
    body = {"ok": alive, "api_v2": "alive" if alive else "dead", "busy": _busy}
    if not alive:
        log.error("health check: api_v2 process pid=%s is not alive", API_V2_PID)
    return JSONResponse(body, status_code=200 if alive else 503)


# ---------------------------------------------------------------- 音频下载

@mcp.custom_route("/audio/{fid}", methods=["GET"])
async def audio(request: Request):
    fid = request.path_params["fid"]
    if not fid.isalnum():
        return JSONResponse({"error": "bad id"}, status_code=400)
    exp_raw = request.query_params.get("exp", "")
    sig = request.query_params.get("sig", "")
    if not _verify_audio_sig(fid, exp_raw, sig):
        return JSONResponse({"error": "unauthorized or expired"}, status_code=401)
    path = os.path.join(OUT_DIR, fid + ".wav")
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="audio/wav")


# ---------------------------------------------------------------- 清理

def _cleanup_expired() -> None:
    now = time.time()
    try:
        names = os.listdir(OUT_DIR)
    except OSError:
        log.error("cleanup: cannot list %s", OUT_DIR, exc_info=True)
        return
    removed = 0
    for name in names:
        if not name.endswith(".wav"):
            continue
        path = os.path.join(OUT_DIR, name)
        try:
            if now - os.path.getmtime(path) > AUDIO_TTL_SECONDS:
                os.remove(path)
                removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            log.error("cleanup: failed to remove %s", path, exc_info=True)
    if removed:
        log.info("cleanup: removed %d expired audio files (ttl=%ds)", removed, AUDIO_TTL_SECONDS)


# ---------------------------------------------------------------- 合成

def _synth(text: str, text_lang: str) -> tuple[str, float, int]:
    """阻塞调用 api_v2 合成，返回 (文件 ID, 时长秒数, 字节数)。只在线程里调用。"""
    params = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": REF_AUDIO,
        "prompt_text": PROMPT_TEXT,
        "prompt_lang": PROMPT_LANG,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read()[:500].decode("utf-8", "replace")
        except Exception:
            log.error("synth: failed to read api_v2 error body status=%s", e.code, exc_info=True)
        raise RuntimeError(f"api_v2 HTTP {e.code}: {body}") from e

    if data[:4] != b"RIFF":
        raise RuntimeError("api_v2 returned non-audio: " + data[:200].decode("utf-8", "replace"))

    fid = uuid.uuid4().hex
    path = os.path.join(OUT_DIR, fid + ".wav")
    with open(path, "wb") as f:
        f.write(data)
    with wave.open(path, "rb") as w:
        seconds = w.getnframes() / w.getframerate()
    return fid, seconds, len(data)


def _audio_url(fid: str) -> str:
    exp = int(time.time()) + AUDIO_TTL_SECONDS
    query = urllib.parse.urlencode({"exp": exp, "sig": _sign(fid, exp)})
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/audio/{fid}?{query}"


@mcp.tool()
async def cove_tts(text: str, text_lang: str = "zh") -> str:
    """用部署时配置的模型和参考音频的音色把文本合成为语音，返回可直接播放/下载的音频 URL。

    Args:
        text: 要合成的文本（最多 500 字，避免叠字词和音译名，模型容易读错）。
        text_lang: 文本语言，"zh" 或 "en"。
    """
    global _busy
    text = text.strip()
    if not text:
        return "错误: 文本为空"
    if len(text) > MAX_TEXT_CHARS:
        return f"错误: 文本过长（{len(text)} 字，上限 {MAX_TEXT_CHARS}）"
    if text_lang not in ("zh", "en"):
        return "错误: text_lang 仅支持 zh / en"
    if not _api_v2_alive():
        log.error("cove_tts: api_v2 pid=%s is dead, text_len=%d", API_V2_PID, len(text))
        return "合成失败: 推理后端 api_v2 已退出，服务需要重启"

    try:
        await asyncio.wait_for(_synth_lock.acquire(), timeout=QUEUE_WAIT_TIMEOUT)
    except asyncio.TimeoutError:
        log.error("cove_tts: waited %ds in queue, giving up. text_len=%d lang=%s",
                  QUEUE_WAIT_TIMEOUT, len(text), text_lang)
        return f"合成失败: 排队超过 {QUEUE_WAIT_TIMEOUT} 秒，前面的任务还没完成"

    _busy = True
    started = time.monotonic()
    try:
        await asyncio.to_thread(_cleanup_expired)
        fid, seconds, nbytes = await asyncio.to_thread(_synth, text, text_lang)
    except Exception as e:
        log.error("cove_tts: synth failed. text_len=%d lang=%s text_head=%r",
                  len(text), text_lang, text[:50], exc_info=True)
        return f"合成失败: {e}"
    finally:
        _busy = False
        _synth_lock.release()

    log.info("cove_tts: ok fid=%s audio=%.1fs bytes=%d cost=%.1fs text_len=%d",
             fid, seconds, nbytes, time.monotonic() - started, len(text))
    hours = AUDIO_TTL_SECONDS / 3600
    return (
        f"合成成功。时长 {seconds:.1f} 秒，大小 {nbytes / 1024:.0f} KB。\n"
        f"音频下载/播放链接（{hours:g} 小时内有效，只能访问这一个文件）: {_audio_url(fid)}\n"
        f"文件 ID: {fid}"
    )


# ---------------------------------------------------------------- 中间件

class ApiKeyMiddleware:
    """/mcp 等端点校验 X-API-Key header、Authorization: Bearer 或 ?key=；/health 免鉴权；
    /audio/ 走自己的签名校验；/.well-known/ 一律 404（不提供 OAuth）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # FastMCP 1.9.x 把 streamable HTTP 挂在 Mount("/mcp") 上，请求 "/mcp" 会被 Starlette 307 重定向到
            # "/mcp/"；服务在 HTTPS 反向代理（比如 Zeabur）后面，重定向容易丢 https 和请求头，客户端直接连不上。
            # 这里把 "/mcp" 原地改写成 "/mcp/"，两种写法都直接命中，不再重定向。
            if scope.get("path") == "/mcp":
                scope = dict(scope)
                scope["path"] = "/mcp/"
                scope["raw_path"] = b"/mcp/"
            path = scope.get("path", "")
            # MCP 客户端收到 401 后会去 /.well-known/oauth-* 找 OAuth 配置。本服务不用 OAuth，
            # 这些地址必须返回 404（"没有 OAuth"），返回 401 会让客户端以为 OAuth 坏了而直接放弃连接。
            if path.startswith("/.well-known/"):
                resp = JSONResponse({"error": "not found"}, status_code=404)
                await resp(scope, receive, send)
                return
            if path != "/health" and not path.startswith("/audio/"):
                headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                           for k, v in scope.get("headers", [])}
                qs = urllib.parse.parse_qs(scope.get("query_string", b"").decode("latin-1"))
                auth = headers.get("authorization", "")
                bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
                key = headers.get("x-api-key") or bearer or (qs.get("key") or [""])[0]
                if not _key_ok(key):
                    log.warning("auth rejected path=%s method=%s has_x_api_key=%s has_bearer=%s has_query_key=%s",
                                path, scope.get("method"), "x-api-key" in headers, bool(bearer), "key" in qs)
                    resp = JSONResponse({"error": "unauthorized"}, status_code=401)
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    app = mcp.streamable_http_app()
    app.add_middleware(ApiKeyMiddleware)
    log.info("serving on 0.0.0.0:9881, endpoint /mcp, audio ttl=%ds, api_v2 pid=%s",
             AUDIO_TTL_SECONDS, API_V2_PID)
    import uvicorn
    # 认反向代理（比如 Zeabur）传来的 X-Forwarded-Proto/For，任何重定向和生成的链接都保持 https
    uvicorn.run(app, host="0.0.0.0", port=9881, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")
