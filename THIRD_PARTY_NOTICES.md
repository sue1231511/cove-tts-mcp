# 第三方说明

## GPT-SoVITS

`app/` 下的推理代码来自 [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)（有裁剪），按 MIT 许可使用：

```
MIT License

Copyright (c) 2024 RVC-Boss

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

子目录里自带许可文件的部分（`app/GPT_SoVITS/BigVGAN/`、`app/tools/AP_BWE_main/`）以各自的 LICENSE 为准。

## 构建时下载的模型

本仓库不包含任何模型权重，`download_models.sh` 在构建镜像时从原地址下载，各自遵循原发布方的许可和使用说明：

| 内容 | 来源 |
|---|---|
| Gove 微调权重（GPT + SoVITS） | HuggingFace `OmniDimen/Gove`，模型页标注 MIT，并注明仅供学习交流 |
| GPT-SoVITS 底模（chinese-roberta-wwm-ext-large、chinese-hubert-base、sv） | HuggingFace `lj1995/GPT-SoVITS` |
| G2PW 中文注音模型 | HuggingFace `XXXXRT/GPT-SoVITS-Pretrained` |
| 语种检测 lid.176.bin | fastText 官方下载地址 |

用这个服务合成声音时，请遵守模型发布方的说明，不要用来冒充真实的人。
