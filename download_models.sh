#!/bin/sh
# 构建期下载全部模型权重。所有大文件做字节数校验，防止静默截断。
set -e
cd /app
HF="https://huggingface.co"

fetch() { # fetch <url> <dest> <expected_bytes 或 0=不校验>
    url="$1"; dest="$2"; want="$3"
    echo ">> $dest"
    # --retry-all-errors: 默认 --retry 不重试 HTTP/2 流被重置（curl 92）这类错误；
    # -C -: 重试时从已下载的位置续传，大文件断一次不用从头再来。
    curl -fL --retry 8 --retry-delay 5 --retry-all-errors -C - --connect-timeout 30 -o "$dest" "$url"
    if [ "$want" != "0" ]; then
        got=$(stat -c%s "$dest")
        if [ "$got" != "$want" ]; then
            echo "SIZE MISMATCH: $dest got=$got want=$want" >&2
            exit 1
        fi
    fi
}

mkdir -p GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large \
         GPT_SoVITS/pretrained_models/chinese-hubert-base \
         GPT_SoVITS/pretrained_models/sv \
         GPT_SoVITS/pretrained_models/fast_langdetect \
         GPT_SoVITS/text \
         GPT_weights_v2Pro SoVITS_weights_v2Pro

# --- Gove 微调权重（HuggingFace OmniDimen/Gove，MIT，模型页注明仅供学习交流）---
fetch "$HF/OmniDimen/Gove/resolve/main/Gove.ckpt" GPT_weights_v2Pro/Gove-e15.ckpt 155313011
fetch "$HF/OmniDimen/Gove/resolve/main/Gove.pth"  SoVITS_weights_v2Pro/Gove-e8.pth  172765299

# --- GPT-SoVITS 底模 (lj1995/GPT-SoVITS) ---
# 不下载 BigVGAN：它只在 v3 模型时由 TTS.init_vocoder 加载，Gove 是 v2ProPlus 用不到（省约 450MB）。
# 以后换 v3 模型需要把 models--nvidia--bigvgan_v2_24khz_100band_256x 加回来。
P="$HF/lj1995/GPT-SoVITS/resolve/main"
fetch "$P/chinese-roberta-wwm-ext-large/config.json"       GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/config.json 963
fetch "$P/chinese-roberta-wwm-ext-large/pytorch_model.bin" GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin 651225145
fetch "$P/chinese-roberta-wwm-ext-large/tokenizer.json"    GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/tokenizer.json 268962
fetch "$P/chinese-hubert-base/config.json"                 GPT_SoVITS/pretrained_models/chinese-hubert-base/config.json 1449
fetch "$P/chinese-hubert-base/preprocessor_config.json"    GPT_SoVITS/pretrained_models/chinese-hubert-base/preprocessor_config.json 212
fetch "$P/chinese-hubert-base/pytorch_model.bin"           GPT_SoVITS/pretrained_models/chinese-hubert-base/pytorch_model.bin 188811417
fetch "$P/sv/pretrained_eres2netv2w24s4ep4.ckpt"           GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt 107528697

# --- 语种检测 (fasttext lid.176.bin) ---
fetch "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin" \
      GPT_SoVITS/pretrained_models/fast_langdetect/lid.176.bin 131266198

# --- G2PW 中文注音模型 ---
# 用 GPT-SoVITS 官方 install.sh 的 HuggingFace 源（XXXXRT/GPT-SoVITS-Pretrained）。
# 原来的 modelscope 源在国内，从海外服务器下载只有 ~200KB/s，561MB 要 48 分钟，
# 而且中途 HTTP/2 流被重置导致构建失败。
fetch "$HF/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip" /tmp/G2PWModel.zip 588856634
rm -rf GPT_SoVITS/text/G2PWModel
unzip -q -o /tmp/G2PWModel.zip -d GPT_SoVITS/text
rm /tmp/G2PWModel.zip
# 压缩包内顶层目录名以官方 install.sh 的用法为准（G2PWModel）；万一是带版本号的目录名，统一改成 G2PWModel
if [ ! -d GPT_SoVITS/text/G2PWModel ] && [ -d GPT_SoVITS/text/G2PWModel_1.1 ]; then
    mv GPT_SoVITS/text/G2PWModel_1.1 GPT_SoVITS/text/G2PWModel
fi
[ -f GPT_SoVITS/text/G2PWModel/g2pW.onnx ] || { echo "G2PWModel missing g2pW.onnx" >&2; ls -la GPT_SoVITS/text >&2; exit 1; }

echo "ALL MODELS OK"
