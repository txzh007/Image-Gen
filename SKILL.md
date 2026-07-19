---
name: genimg
description: 生成或编辑图片和视频。当用户要求“生图、画图、文生图、图生图、图片编辑、生成视频、即梦视频、Seedance、文生视频、图生视频、视频续作、edit image、generate image、generate video”时使用。图片支持 banana 与 image2；视频支持异步 video-ds-2.0 与 video-ds-2.0-fast 任务。
---

# genimg — 图片与视频生成 skill

使用零依赖 Python 脚本调用中转站：`genimg.py` 处理图片，`genvideo.py` 处理异步视频任务。适用于 Claude Code / Codex / OpenCode 等任意能运行 shell 的 agent。

## 首次配置（只做一次）

macOS 推荐直接运行配置脚本。它把 key 存入 macOS Keychain，并配置 `.zprofile` 与 `.zshrc`：

```bash
bash configure-macos.sh
source "$HOME/.genimg-env.zsh"
```

需要填写两项：`IMAGE_API_BASE`（通常以 `/v1` 结尾）和 `GENIMG_API_KEY`。内置 provider 已在 `providers.example.json` 中配置：

- `banana`：`gemini-3-pro-image`，默认入口，优先高质量和复杂指令遵循。
- `image2`：`gpt-image-2`。
- 即梦视频：`video-ds-2.0-fast`（默认快速版）与 `video-ds-2.0`（标准版）。

其他平台或需要多个 provider 时按以下方式手动配置：

1. 复制 `providers.example.json` 为 `providers.json`，填入各分组的 `base_url` / `model` / `mode`。可在 `defaults` 中保存该 provider 的默认生图参数。
2. 设置中转站地址和 API key；需要 provider 独立 key 时再修改 `api_key_env`：

   ```bash
   export IMAGE_API_BASE="https://你的中转站/v1"
   export GENIMG_API_KEY="sk-xxx"
   ```

3. **不确定接口格式时**，先跑一次 debug 看真实返回，再定 `mode`：

   ```bash
   python3 genimg.py "a cat" --provider image2 --debug --no-proxy
   ```

   - 返回里是 `b64_json` / `url` → 用 `--mode images`
   - 返回像聊天消息、图片藏在 markdown 或 `image_url` 里 → 用 `--mode chat`
   - 返回里有 `inlineData` → 用 `--mode gemini`
   
   **常见问题**：遇到 Cloudflare 1010 错误或代理干扰时加 `--no-proxy`。

## 用法

```bash
# 单个分组
python3 genimg.py "一只戴墨镜的柴犬" --provider banana

# 同时用两个分组出图（各存一张，方便对比）
python3 genimg.py "赛博朋克城市夜景" --provider banana,image2

# 配置里全部分组
python3 genimg.py "森林里的小屋" --provider all

# OpenAI Images：显式传递尺寸、质量、数量、透明背景和输出编码
python3 genimg.py "透明底产品图" --provider image2 \
  --size 1536x1024 --quality high --n 2 \
  --background transparent --output-format webp

# Banana：使用比例和 K 级分辨率
python3 genimg.py "竖版电影海报" --provider banana \
  --aspect-ratio 9:16 --quality 2K

# 编辑图片：出现 --image 时自动使用 provider 的 edit_mode/edit_endpoint
python3 genimg.py "只把背景改成夜晚球场，保持人物、服装、姿势和构图不变" \
  --provider banana --image ./input.png --out ./output/banana-edit

# image2 编辑；多张参考图可重复使用 --image
python3 genimg.py "保留人物身份与面部特征，把球衣改成蓝白配色" \
  --provider image2 --image ./input.png --quality high --out ./output/image2-edit

# 带遮罩的局部编辑
python3 genimg.py "只在透明遮罩区域添加一个足球" \
  --provider image2 --image ./input.png --mask ./mask.png

# 中转站私有参数，值按 JSON 解析；支持点路径和重复传入
python3 genimg.py "海报" --provider banana \
  --param seed=42 --param google.image_config.person_generation=allow_adult

# 只检查最终请求体，不联网
python3 genimg.py "test" --provider image2 --quality high --dry-run

# 指定输出文件名
python3 genimg.py "logo" --provider banana --out ./assets/logo

# 查看已配置分组
python3 genimg.py --list
```

图片默认存到 `./output/<provider>_<时间戳>.<ext>`。

## 视频任务

使用 `genvideo.py` 创建任务、自动轮询并下载 MP4：

```bash
# 默认快速版，5 秒、16:9
python3 genvideo.py "橙色小猫坐在雨夜霓虹屋顶，镜头缓慢推进，电影感" \
  --model video-ds-2.0-fast \
  --seconds 5 \
  --aspect-ratio 16:9 \
  --out ./output/cat.mp4

# 10 秒竖屏视频
python3 genvideo.py "模特走过未来城市街道，稳定跟拍，无水印" \
  --seconds 10 --aspect-ratio 9:16 --out ./output/city.mp4

# 创建任务后立即返回，不等待
python3 genvideo.py "海边日出延时摄影" --seconds 10 --no-wait

# 中断后续查已有任务；不要重新创建付费任务
python3 genvideo.py --task-id task_xxx --out ./output/resumed.mp4
```

参考图片、视频和音频必须是公网 URL；重复相应参数传入：

```bash
python3 genvideo.py "保持参考猫的形象，让它穿披风飞向城市" \
  --seconds 10 \
  --image https://example.com/cat.png \
  --video https://example.com/motion.mp4 \
  --audio https://example.com/music.mp3
```

本地文件路径不能直接放进视频任务 JSON。先上传到中转站素材库或可公开访问的对象存储，再传 URL。

## 给 agent 的执行提示

- 用户没指定 provider 时使用 `banana`；用户点名 `banana` 或 `image2` 时严格使用对应 provider，不要覆盖模型或偷换 provider。
- 用户说「都试试 / 对比」时使用 `--provider banana,image2` 或 `all`。
- provider 返回 `model_not_found` 或无可用渠道时，原样报告；不要自动改用另一种方式。
- 不要只把尺寸、比例、张数、透明背景或格式写进 prompt。用户明确提出这些要求时，必须转换成对应 CLI 参数。
- 用户没提质量/尺寸时不要擅自用 4K；优先采用 provider 的 `defaults`，避免额外费用。
- OpenAI Images 使用 `--size/--quality/--n/--background/--output-format`。`--response-format` 只适用于 DALL-E 2/3 的 `url|b64_json`，GPT Image 默认返回 base64。
- 内置 `banana` 使用 `--aspect-ratio` 和 `--quality 1K|2K|4K`；脚本会写入中转站的 `extra_fields.google.image_config`。
- 内置 `banana` 与 `image2` 都支持图片编辑：传 `--image` 后自动使用 provider 的 `edit_mode` / `edit_endpoint`。banana 使用 Chat 多模态 data-URI；image2 使用 `/images/edits` multipart 的可重复 `image[]`。
- 编辑提示词要明确列出“改什么”和“必须保持什么”。需要多张参考图时重复 `--image`。局部遮罩 `--mask` 仅用于 `edit_mode=images`（内置 image2）；遮罩应与第一张输入图同尺寸、同格式且带 alpha 通道。
- `chat` 没有统一的生图参数协议。中转站要求私有字段时用 `--param`，或写在 provider 的 `extra_body`，不要假设 OpenAI Images 参数会自动生效。
- 请求失败或怀疑参数未生效时先加 `--dry-run` 检查请求体，再用 `--debug` 查看响应。
- 跑完把保存路径回给用户，并说明实际 provider、model、尺寸/质量等关键参数；若脚本提示参数被忽略，也要明确告知用户。
- 用户要求视频、即梦、Seedance、文生视频、图生视频或视频续作时，运行 `genvideo.py`，不要用 `genimg.py`。
- 用户没指定视频模型时使用 `video-ds-2.0-fast`；只有明确要标准版时才使用 `video-ds-2.0`。
- 视频是付费异步任务。创建成功后立即记录 `task_id` 并持续轮询；中断或超时后使用 `--task-id` 续查，绝不因等待中断而重复创建。
- 用户指定时长和比例时必须使用 `--seconds` 与 `--aspect-ratio`，不要只写进 prompt。创建前若返回余额不足，报告所需与剩余额度，不重复尝试相同任务。
- 遇到 `PROVIDER_MODERATION_ERROR` 时原样报告。仅在能保持用户主要视觉意图时，把受限角色名称或标志改写为原创描述并最多重试一次，避免重复扣费。
- 视频参考素材只接受公网 URL。不要把本地路径传给 `--image/--video/--audio`；先取得素材 URL。
- `/content` 下载失败时让脚本使用任务返回的临时 `video_url` 回退；不要把 API key 转发给第三方对象存储 URL。
- 视频完成后回报任务 ID、模型、实际时长、分辨率、编码和本地绝对路径；能使用 `ffprobe` 时执行一次媒体校验。
- 不要把 API key 打印或写进任何文件。

## 参数与 mode

| 参数 | images | gemini | chat |
|---|---|---|---|
| `--image, -i` | 使用 provider 的编辑路由，可重复 | 输入图，可重复 | 输入图，可重复 |
| `--mask` | 编辑遮罩，仅与 `--image` 一起使用 | 不支持 | 不支持 |
| `--size` | 像素尺寸，如 `1536x1024` | `1K/2K/4K`；比例请优先用下项 | 无通用映射 |
| `--aspect-ratio` | 中转站 `extra_fields` 扩展 | `1:1/16:9/9:16` 等 | 无通用映射 |
| `--quality` | `auto/low/medium/high`；中转站也可用 `2K/4K` | `1K/2K/4K`（兼容旧写法） | 无通用映射 |
| `--n` | 生成 1-10 张 | 仅 1 张 | Chat Completions 的 `n` |
| `--response-format` | DALL-E 2/3：`url/b64_json` | 无通用映射 | 无通用映射 |
| `--output-format` | GPT Image：`png/jpeg/webp` | 无通用映射 | 无通用映射 |
| `--output-compression` | GPT Image JPEG/WEBP：`0-100` | 无通用映射 | 无通用映射 |
| `--background` | GPT Image：`auto/transparent/opaque` | 无通用映射 | 无通用映射 |
| `--moderation` | GPT Image：`auto/low` | 无通用映射 | 无通用映射 |
| `--style` | DALL-E 3：`vivid/natural` | 无通用映射 | 无通用映射 |

通用控制参数：`--provider/-p`、`--mode`、`--edit-mode`、`--model/-m`、`--base-url`、`--endpoint`、`--edit-endpoint`、`--api-key`、`--config`、`--out/-o`、`--outdir`、`--timeout`、`--no-proxy`、`--debug`、`--dry-run`、`--list`。

`--param KEY=VALUE` 会在最后深度合并到请求体。完整优先级为：`--param` > 结构化 CLI 参数 > `defaults` > provider 顶层旧式参数 > `extra_body` > 内置默认。

## 视频参数

| 参数 | 说明 |
|---|---|
| `prompt` | 视频内容、动作、镜头、风格与限制 |
| `--model` | `video-ds-2.0-fast`（默认）或 `video-ds-2.0` |
| `--seconds` | 时长，例如 `5`、`10`、`15` |
| `--aspect-ratio` | `16:9`、`9:16`、`1:1` |
| `--image` | 公网参考图片 URL，可重复 |
| `--video` | 公网参考视频 URL，可重复 |
| `--audio` | 公网参考音频 URL，可重复 |
| `--task-id` | 续查已有任务，不创建新任务 |
| `--no-wait` | 创建后只返回 task ID |
| `--poll-interval` | 轮询间隔，默认 15 秒 |
| `--wait-timeout` | 总等待上限，默认 900 秒 |
| `--out` | MP4 输出路径 |
| `--dry-run` | 打印请求，不创建付费任务 |
