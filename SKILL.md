---
name: genimg
description: 通过 Sub2API 生成和编辑图片、创建和续查异步视频任务。用户要求生图、画图、文生图、图生图、图片编辑、Gemini 图片、GPT Image、即梦视频、Seedance、文生视频、图生视频或视频续作时使用。
---

# genimg

使用本目录中的零依赖 Python 3 脚本：

- `genimg.py`：生成或编辑图片。
- `genvideo.py`：创建、轮询、续查并下载视频任务。
- `upload.py`：经用户同意后，把视频参考素材上传到第三方图床。

在运行命令时使用 Skill 目录中的脚本绝对路径；不要假设当前工作目录就是 Skill
目录。让输出路径指向用户当前工作目录或用户明确指定的位置。

## 配置

只从环境变量读取中转地址和用户 Key：

```bash
export IMAGE_API_BASE="https://你的中转站/v1"
export GENIMG_API_KEY="sk-xxx"
```

要求 `IMAGE_API_BASE` 以 `/v1` 结尾。只使用 `IMAGE_API_BASE` 和
`GENIMG_API_KEY`，不要读取配置文件 Key 或上游 Key，
不要使用已禁用的 `--base-url`、`--api-key`、`--endpoint`、`--edit-endpoint` 绕过
中转。首次使用时可以执行不产生费用的检查：

```bash
python3 genimg.py "配置检查" --provider banana --dry-run
python3 genvideo.py "配置检查" --dry-run
```

## 图片工作流

根据用户意图选择 provider：

- 默认使用 `banana`，模型为 `gpt-image-gemini-flash`。
- 只有用户明确要求 Pro 或更高质量时，才添加
  `--model gpt-image-gemini-pro`。Pro 失败时不要静默降级。
- 用户点名 GPT Image、image2 或 `gpt-image-2` 时使用 `image2`。
- 用户明确要求比较两种结果时使用 `--provider banana,image2`。

文字生图固定调用 Sub2 `/images/generations`，图片修改固定调用 Sub2
`/images/edits` multipart。只发送 Sub2 别名；不要发送真实 Gemini 上游模型名，
不要改用 `/chat/completions`，不要直连上游。

常用命令：

```bash
# 默认生图
python3 genimg.py "一只戴墨镜的柴犬" --provider banana

# GPT Image
python3 genimg.py "透明底产品图" --provider image2 \
  --size 1536x1024 --quality high --background transparent

# 图片编辑
python3 genimg.py "只把背景改成夜晚城市，保持主体与构图不变" \
  --provider banana --image ./input.png --out ./output/edited

# 多张参考图
python3 genimg.py "保留第一张主体，采用第二张配色" \
  --provider image2 --image ./subject.png --image ./style.png
```

`--image` 接受本地文件、HTTP(S) URL、Data URL 或纯 Base64，可重复。脚本会把 URL
下载为图片字节，把 Base64 解码为图片字节，校验文件签名后统一作为 multipart
文件字段 `image` 上传。不要把 URL 或 Base64 当成普通文本字段发送。使用 `--mask`
进行局部编辑时，让遮罩与第一张图片保持相同尺寸和格式并包含 alpha 通道。

把用户明确提出的尺寸、质量、数量、比例、背景和格式转换为参数，不要只写进提示词：

- `--size`、`--quality`、`--n`
- `--aspect-ratio`
- `--background`、`--output-format`、`--output-compression`
- `--mask`

用户没有提出时采用 provider 默认值，不要擅自使用 Pro 或 4K。需要查看全部参数时运行
`python3 genimg.py --help`。

## 视频工作流

用户要求即梦、Seedance 或视频生成时运行 `genvideo.py`：

```bash
# 默认快速版，创建后持续轮询并下载
python3 genvideo.py "雨夜城市上空飞行，镜头缓慢推进" \
  --model video-ds-2.0-fast --seconds 5 --aspect-ratio 16:9 \
  --out ./output/result.mp4

# 创建后立即返回
python3 genvideo.py "海边日出" --seconds 10 --no-wait

# 续查已有任务，禁止重复创建
python3 genvideo.py --task-id task_xxx --out ./output/resumed.mp4
```

默认使用 `video-ds-2.0-fast`；只有用户明确要求标准版时使用 `video-ds-2.0`。任务创建
成功后立即记录 `task_id`。等待中断或超时后用 `--task-id` 续查，绝不因为中断而重复
创建付费任务。

视频参考图片、视频和音频必须是公网 URL。用户提供本地文件时，先说明文件将上传到
所选第三方服务并取得同意，再使用 `--auto-upload catbox` 或用户指定的服务。不要在
未经确认的情况下公开上传本地素材。需要查看全部参数时运行
`python3 genvideo.py --help`。

## 错误处理与安全

- 对图片请求的 HTTP 429/503 保持原 provider 和模型，按 2、4、8 秒最多重试三次。
- 原样报告 `model_not_found`、无可用渠道、内容审核和余额不足；不要偷换收费模型。
- 遇到 `images endpoint requires an image model` 时检查是否误用了真实 Gemini 模型名。
- 视频创建成功后即使轮询失败也不要重新创建；返回任务 ID并说明续查命令。
- 仅在排查时使用 `--debug`；不要输出完整 Key、Base64 或 Data URL。
- 不要把中转 Authorization 头转发给任务返回的第三方对象存储 URL。
- 图片完成后返回 provider、模型和本地绝对路径。
- 视频完成后返回任务 ID、模型、时长、输出路径；可用时通过 `ffprobe` 验证媒体。

完整 CLI 细节以脚本 `--help` 为准。只有需要调整 provider 默认参数时才复制
`providers.example.json` 为 `providers.json`；不要在其中写入地址或 Key。
