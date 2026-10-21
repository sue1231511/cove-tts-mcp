# -*- coding: utf-8 -*-
"""启动前准备参考音频，由 start.sh 在拉起 api_v2 之前调用。

GPT-SoVITS 每次合成都必须带一段参考音频和它对应的文字，本仓库不附带参考音频，
由部署者通过环境变量提供：

- REF_AUDIO_URL   （可选）参考音频的下载地址。设置了就在启动时下载到 REF_AUDIO_PATH，覆盖已有文件。
- REF_AUDIO_PATH  （可选）参考音频在容器里的路径，默认 /app/ref/ref.wav。
                   不用 URL 时，把音频放进仓库的 app/ref/ref.wav 再构建，或者挂载到这个路径。
- REF_PROMPT_TEXT （必填）参考音频里说的那句话，一字不差。
- REF_PROMPT_LANG （可选）参考音频的语言，zh 或 en，默认 zh。

任何一项不满足都以非 0 退出，容器直接起不来，日志里写清楚原因，
免得 api_v2 花几分钟加载完模型后，第一次合成才报错。
"""
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("prepare-ref")

DEFAULT_REF_PATH = "/app/ref/ref.wav"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_RETRIES = 3
# GPT-SoVITS 的 TTS._set_prompt_semantic 只接受 3~10 秒的参考音频，超出范围在合成时抛错
MIN_SECONDS = 3.0
MAX_SECONDS = 10.0
LANGS = ("zh", "en")


def _mask_url(url: str) -> str:
    """日志里只留协议、域名和路径，去掉查询参数（可能带签名或 token）。"""
    try:
        parts = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return "<invalid url>"


def _download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".part"
    last_err = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cove-tts-mcp/1.0"})
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(tmp, "wb") as f:
                total = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError(f"文件超过 {MAX_DOWNLOAD_BYTES // 1024 // 1024}MB 上限")
                    f.write(chunk)
            if total == 0:
                raise RuntimeError("下载到的文件是空的")
            os.replace(tmp, dest)
            log.info("参考音频下载完成 url=%s bytes=%d dest=%s", _mask_url(url), total, dest)
            return
        except (urllib.error.URLError, OSError, RuntimeError) as e:
            last_err = e
            log.error("参考音频下载失败 第%d/%d次 url=%s dest=%s",
                      attempt, DOWNLOAD_RETRIES, _mask_url(url), dest, exc_info=True)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                log.error("清理下载临时文件失败 tmp=%s", tmp, exc_info=True)
            if attempt < DOWNLOAD_RETRIES:
                time.sleep(3 * attempt)
    raise RuntimeError(f"参考音频下载失败：{last_err}")


def _read_seconds(path: str) -> float | None:
    """先用 soundfile（镜像里装了，支持 wav/flac/ogg 等），不行再用标准库 wave（只认 PCM wav）。都读不出返回 None。"""
    try:
        import soundfile
        info = soundfile.info(path)
        return info.frames / float(info.samplerate)
    except Exception:
        log.warning("soundfile 读不出参考音频时长，改用 wave 再试 path=%s", path, exc_info=True)
    try:
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        log.warning("wave 也读不出参考音频时长 path=%s", path, exc_info=True)
    return None


def _check_duration(path: str) -> None:
    """能读出时长就检查 3~10 秒；读不出来（比如 mp3）只警告，交给 GPT-SoVITS 自己读。"""
    seconds = _read_seconds(path)
    if seconds is None:
        log.warning("读不出参考音频时长，跳过时长检查（建议用 wav）path=%s", path)
        return
    if not (MIN_SECONDS <= seconds <= MAX_SECONDS):
        raise RuntimeError(
            f"参考音频时长 {seconds:.1f} 秒，GPT-SoVITS 只接受 {MIN_SECONDS:g}~{MAX_SECONDS:g} 秒，请换一段"
        )
    log.info("参考音频时长 %.1f 秒 path=%s", seconds, path)


def main() -> int:
    ref_url = os.environ.get("REF_AUDIO_URL", "").strip()
    ref_path = os.environ.get("REF_AUDIO_PATH", "").strip() or DEFAULT_REF_PATH
    prompt_text = os.environ.get("REF_PROMPT_TEXT", "").strip()
    prompt_lang = (os.environ.get("REF_PROMPT_LANG", "").strip() or "zh").lower()

    if not prompt_text:
        log.error("REF_PROMPT_TEXT 没有设置：要填参考音频里说的那句话，一字不差")
        return 1
    if prompt_lang not in LANGS:
        log.error("REF_PROMPT_LANG=%r 不支持，只能是 %s", prompt_lang, " / ".join(LANGS))
        return 1

    if ref_url:
        try:
            _download(ref_url, ref_path)
        except RuntimeError:
            log.error("拿不到参考音频，停止启动 url=%s path=%s", _mask_url(ref_url), ref_path, exc_info=True)
            return 1

    if not os.path.isfile(ref_path):
        log.error(
            "找不到参考音频 path=%s。设置 REF_AUDIO_URL 让启动时下载，"
            "或者把音频放到仓库的 app/ref/ref.wav 再构建，或者挂载到这个路径",
            ref_path,
        )
        return 1
    if os.path.getsize(ref_path) == 0:
        log.error("参考音频是空文件 path=%s", ref_path)
        return 1

    try:
        _check_duration(ref_path)
    except RuntimeError:
        log.error("参考音频不合格 path=%s", ref_path, exc_info=True)
        return 1

    log.info("参考音频就绪 path=%s prompt_lang=%s prompt_text_len=%d", ref_path, prompt_lang, len(prompt_text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
