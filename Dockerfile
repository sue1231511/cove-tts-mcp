# syntax=docker/dockerfile:1
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl unzip g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

# g2p_en（英文注音）运行时依赖 nltk 资源。不在构建期装好的话，第一次合成带英文的句子时
# g2p_en 会现场联网下载；而且新版 nltk 的 pos_tag 要的是 averaged_perceptron_tagger_eng，
# g2p_en 自己只会下旧名字，会在运行时报 LookupError。这里一次装齐并实际跑一句验证。
RUN python -c "import nltk; [nltk.download(r, quiet=True, raise_on_error=True) for r in ('averaged_perceptron_tagger', 'averaged_perceptron_tagger_eng', 'cmudict')]" \
    && python -c "from g2p_en import G2p; print('g2p_en ok:', G2p()('hello world'))"

# 构建期拉取全部模型权重：Gove 微调权重 + GPT-SoVITS 底模 + G2PW 词典。
# 放在 COPY app/ 之前：模型层不受代码改动影响，改代码重新构建时不用再下一遍 ~1.8GB。
COPY download_models.sh /tmp/download_models.sh
RUN sh /tmp/download_models.sh && rm /tmp/download_models.sh

COPY app/ /app/
COPY mcp_server.py /app/mcp_server.py
COPY prepare_ref.py /app/prepare_ref.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# 构建期按 api_v2 相同的 sys.path 导入推理模块（只导入不加载模型），
# 缺包、语法错误在构建阶段就失败，而不是等容器启动后 api_v2 才崩。
RUN python -c "import os, sys; sys.path[:0] = [os.getcwd(), os.getcwd() + '/GPT_SoVITS']; from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config; print('TTS import ok')"

EXPOSE 9881

CMD ["/app/start.sh"]
