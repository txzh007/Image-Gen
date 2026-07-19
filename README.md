# genimg — banana / image2 图片生成与编辑

本项目提供两个零第三方依赖 CLI：`genimg.py` 生成/编辑图片，`genvideo.py` 创建、轮询并下载异步视频任务。可供 Codex、Claude Code、OpenCode 或终端直接调用。

内置两个 provider：

| provider | 默认模型 | 文生图入口 | 图片编辑入口 | 适合场景 |
|---|---|---|---|---|
| `banana` | `gemini-3-pro-image` | `/images/generations` | `/chat/completions` 多模态 | 高质量、复杂指令、文字与构图 |
| `image2` | `gpt-image-2` | `/images/generations` | `/images/edits` multipart | 高保真编辑、遮罩、多参考图 |

视频使用 `video-ds-2.0-fast`（默认快速版）或 `video-ds-2.0`（标准版），接口为异步 `POST /v1/videos`。

未指定 `--provider` 时默认使用 `banana`。点名 provider 后，脚本不会偷偷换模型或切换到另一种方式。

## 最快开始

进入项目目录，准备好中转站地址和 API key：

```bash
cd /path/to/Image-Gen
export IMAGE_API_BASE="https://你的中转站/v1"
export GENIMG_API_KEY="sk-xxx"
```

生成一张图片：

```bash
python3 genimg.py "一只戴墨镜的柴犬，电影感摄影" --provider banana
```

编辑一张已有图片：

```bash
python3 genimg.py "只把背景改成夕阳下的足球场，保持人物、服装、姿势和构图不变" \
  --provider banana \
  --image ./input.png \
  --out ./output/edited
```

图片默认保存到 `./output/`。脚本会识别服务端返回的真实图片格式，因此 `--out` 可以不写扩展名。

生成视频：

```bash
python3 genvideo.py "橙色小猫坐在雨夜霓虹屋顶，镜头缓慢推进，电影感" \
  --seconds 5 --aspect-ratio 16:9 --out ./output/cat.mp4
```

## 1. 安装

要求：Python 3，无需 `pip install`。

### 作为普通命令行工具

```bash
git clone https://github.com/txzh007/Image-Gen.git
cd Image-Gen
python3 genimg.py --help
```

### 安装为 Codex skill

```bash
git clone https://github.com/txzh007/Image-Gen.git "$HOME/.codex/skills/genimg"
```

安装后重启 Codex。之后可以直接对 Codex 说：

- “用 banana 生成一张 16:9 的电影海报”
- “用 image2 把这张图片背景改成海边”
- “banana 和 image2 都生成一张让我比较”

### 安装为 Claude Code / OpenCode skill

```bash
# Claude Code
git clone https://github.com/txzh007/Image-Gen.git "$HOME/.claude/skills/genimg"

# OpenCode
git clone https://github.com/txzh007/Image-Gen.git "$HOME/.opencode/skills/genimg"
```

如果仓库已经下载到本地，也可以从仓库目录运行：

```bash
GENIMG_REPO="https://github.com/txzh007/Image-Gen.git" bash install.sh
```

## 2. 配置 API

必须提供：

- `IMAGE_API_BASE`：OpenAI-compatible 中转站地址，通常以 `/v1` 结尾。
- `GENIMG_API_KEY`：中转站 API key。

### macOS 推荐配置

运行：

```bash
bash configure-macos.sh
source "$HOME/.genimg-env.zsh"
```

脚本会：

1. 询问 `IMAGE_API_BASE` 和 `GENIMG_API_KEY`。
2. 把 API key 保存到 macOS Keychain，不把明文 key 写进 shell 配置。
3. 创建 `~/.genimg-env.zsh`。
4. 自动在 `~/.zprofile` 和 `~/.zshrc` 中加载该环境文件。
5. 执行一次不联网的 dry-run 检查。

配置完成后，新终端会自动加载；当前终端需要执行一次：

```bash
source "$HOME/.genimg-env.zsh"
```

只预览配置动作、不写入 Keychain 或配置文件：

```bash
IMAGE_API_BASE="https://relay.example/v1" GENIMG_API_KEY="sk-test" \
  bash configure-macos.sh --dry-run
```

### Linux / 临时配置

```bash
export IMAGE_API_BASE="https://你的中转站/v1"
export GENIMG_API_KEY="sk-xxx"
```

如果要长期保存，可以把 export 命令加入自己的 shell 配置；注意保护 API key，不要提交到 Git。

### Windows PowerShell

```powershell
$env:IMAGE_API_BASE="https://你的中转站/v1"
$env:GENIMG_API_KEY="sk-xxx"
python genimg.py "一只柴犬" --provider banana
```

### 检查最终请求，不产生费用

```bash
python3 genimg.py "配置检查" --provider banana,image2 --dry-run
```

输出应显示：

- banana：`model=gemini-3-pro-image`
- image2：`model=gpt-image-2`
- URL 使用你配置的 `IMAGE_API_BASE`

仅使用内置 banana/image2 时，不需要创建 `providers.json`；环境变量会覆盖模板中的示例地址。

## 3. 文生图

### banana：默认高质量方式

```bash
python3 genimg.py "雨夜东京街道，电影感，霓虹灯倒影，写实摄影" \
  --provider banana
```

指定比例与分辨率：

```bash
python3 genimg.py "高端汽车广告，山路日出，商业摄影" \
  --provider banana \
  --aspect-ratio 16:9 \
  --quality 4K \
  --out ./output/car-ad
```

banana 文生图常用分辨率为 `1K`、`2K`、`4K`；是否实际输出对应尺寸取决于中转站和模型渠道。

### image2：指定像素尺寸

```bash
python3 genimg.py "极简白色背景上的机械键盘产品图" \
  --provider image2 \
  --size 1536x1024 \
  --quality high \
  --output-format png \
  --out ./output/keyboard
```

生成 JPEG 或 WebP：

```bash
python3 genimg.py "旅行杂志封面，冰岛瀑布" \
  --provider image2 \
  --size 1024x1536 \
  --quality high \
  --output-format webp \
  --output-compression 85
```

`--output-compression` 只适用于 `jpeg` 和 `webp`。

### 同时调用两种方式对比

```bash
python3 genimg.py "未来城市夜景，雨后街道，电影感" \
  --provider banana,image2 \
  --outdir ./output/compare
```

多 provider 模式下，脚本会按 provider 分别命名输出文件。

## 4. 图片编辑

只要传入 `--image`，脚本就会自动进入该 provider 的编辑流程，无需手动设置 `--mode` 或接口地址。

### banana 编辑

```bash
python3 genimg.py "只把背景改成金色夕阳下的足球场；必须保持人物身份、面部特征、发型、姿势、服装和原始构图不变" \
  --provider banana \
  --image ./input.png \
  --out ./output/banana-edited
```

banana 编辑通过 Chat 多模态入口完成。`--quality`、`--aspect-ratio` 在该编辑入口没有通用映射，脚本会提示它们被忽略；这些参数主要用于 banana 文生图。

### image2 编辑

```bash
python3 genimg.py "只把白天改成夜晚，保留建筑结构、相机角度和全部文字" \
  --provider image2 \
  --image ./input.png \
  --quality high \
  --out ./output/image2-edited
```

image2 编辑走 `/images/edits`，支持 `--size`、`--quality`、`--output-format` 等 Images 参数。

### 多张参考图

重复传入 `--image`：

```bash
python3 genimg.py "以第一张图为主体，采用第二张图的服装和第三张图的配色，保持主体面部特征" \
  --provider image2 \
  --image ./person.png \
  --image ./clothes.png \
  --image ./colors.png \
  --out ./output/multi-reference
```

banana 也可以重复使用 `--image`；它们会作为同一条多模态消息中的参考图发送。

### 使用遮罩做局部编辑

`--mask` 仅用于 `edit_mode=images`，内置 provider 中就是 `image2`：

```bash
python3 genimg.py "只在透明遮罩区域添加一个红色足球，其他区域保持不变" \
  --provider image2 \
  --image ./input.png \
  --mask ./mask.png \
  --out ./output/masked-edit
```

遮罩要求：

- 与第一张输入图尺寸相同。
- 与第一张输入图格式相同。
- 包含 alpha 通道；透明区域表示需要编辑的区域。
- 单张文件小于 50 MB。

banana 的 Chat 编辑入口不支持 `--mask`；需要局部编辑时使用 image2。

## 5. 即梦视频任务

视频生成是异步任务，`genvideo.py` 会自动完成：

1. `POST /v1/videos` 创建任务。
2. 保存并打印 `task_id`。
3. 轮询 `GET /v1/videos/{task_id}`。
4. 完成后从 `/content` 下载 MP4。
5. 如果 `/content` 暂时返回 502，自动使用任务返回的临时视频 URL 下载。

### 文生视频

```bash
python3 genvideo.py "雨夜香港街道，一辆黑色跑车驶过霓虹灯，低机位跟拍，写实电影感，无字幕，无水印" \
  --model video-ds-2.0-fast \
  --seconds 10 \
  --aspect-ratio 16:9 \
  --out ./output/hong-kong-car.mp4
```

默认参数：

- 模型：`video-ds-2.0-fast`
- 时长：5 秒
- 比例：16:9
- 轮询间隔：15 秒
- 最长等待：900 秒

需要标准版时显式指定：

```bash
python3 genvideo.py "高端香水广告，玻璃瓶缓慢旋转，奢华灯光" \
  --model video-ds-2.0 --seconds 10
```

### 创建后稍后查询

如果不想一直等待：

```bash
python3 genvideo.py "海边日出延时摄影" --seconds 10 --no-wait
```

脚本会打印任务 ID。之后继续查询同一个任务：

```bash
python3 genvideo.py --task-id task_xxx --out ./output/sunrise.mp4
```

使用 `--task-id` 不会创建新任务，可避免中断后重复扣费。

### 参考图片、视频与音频

参考素材必须是公网可访问 URL，不能直接使用本地文件路径：

```bash
python3 genvideo.py "保持参考图片的人物形象，采用参考视频的动作节奏，生成自然流畅的短片" \
  --seconds 10 \
  --aspect-ratio 9:16 \
  --image https://example.com/person.png \
  --video https://example.com/motion.mp4 \
  --audio https://example.com/music.mp3 \
  --out ./output/reference-video.mp4
```

多个素材重复对应参数：

```bash
--image https://example.com/a.png --image https://example.com/b.png
```

本地素材需要先上传到中转站素材库或对象存储，再把返回 URL 传给脚本。

### 只检查请求、不创建付费任务

```bash
python3 genvideo.py "测试视频" --seconds 10 --aspect-ratio 16:9 --dry-run
```

### 视频常用参数

| 参数 | 说明 | 示例 |
|---|---|---|
| `--model` | 视频模型 | `video-ds-2.0-fast`、`video-ds-2.0` |
| `--seconds` | 视频时长 | `5`、`10`、`15` |
| `--aspect-ratio` | 画面比例 | `16:9`、`9:16`、`1:1` |
| `--image` | 公网参考图片 URL，可重复 | `https://.../a.png` |
| `--video` | 公网参考视频 URL，可重复 | `https://.../a.mp4` |
| `--audio` | 公网参考音频 URL，可重复 | `https://.../a.mp3` |
| `--task-id` | 续查已有任务 | `task_xxx` |
| `--no-wait` | 创建后立即返回任务 ID | — |
| `--poll-interval` | 轮询间隔秒数 | `15` |
| `--wait-timeout` | 总等待上限秒数 | `900` |
| `--out` | MP4 输出路径 | `output/video.mp4` |
| `--dry-run` | 不联网、不创建任务 | — |

视频任务按中转站规则计费。余额不足时接口会在创建前返回所需和剩余额度；不要重复提交相同任务。遇到 `PROVIDER_MODERATION_ERROR` 时需要调整提示词中的受限角色名称或标志。

## 6. 如何写高质量提示词

文生图建议包含：主体、环境、构图、光线、风格、材质和不希望出现的内容。

```text
主体：一位身穿蓝白球衣的成年女球迷
环境：大型足球场看台，比赛刚刚结束
构图：半身近景，人物位于画面中央，背景有自然景深
光线：金色夕阳逆光，面部曝光自然
风格：写实体育摄影，真实皮肤和布料纹理
限制：不要水印，不要额外手指，不要改变球衣号码
```

图片编辑提示词建议明确分成两类：

```text
修改：把背景换成夕阳下的足球场。
保持：人物身份、面部、发型、姿势、服装、画面比例和相机角度全部不变。
```

只写“帮我优化一下”通常会让模型自由发挥；把必须保留的内容逐项列出，编辑结果会更稳定。

## 7. 输出路径与数量

指定单张输出：

```bash
python3 genimg.py "图标设计" --provider banana --out ./assets/icon
```

指定输出目录：

```bash
python3 genimg.py "产品图" --provider image2 --outdir ./output/products
```

生成多张：

```bash
python3 genimg.py "三种不同构图的咖啡海报" \
  --provider image2 \
  --n 3 \
  --outdir ./output/coffee
```

`--n` 范围为 1–10，但实际是否支持多张取决于模型和中转站。

## 8. 图片常用参数

| 参数 | 用途 | 示例 |
|---|---|---|
| `--provider`, `-p` | 选择 provider，可用逗号分隔 | `banana`、`image2`、`banana,image2` |
| `--image`, `-i` | 输入/参考图片，可重复 | `--image input.png` |
| `--mask` | image2 局部编辑遮罩 | `--mask mask.png` |
| `--out`, `-o` | 单张结果的输出路径 | `--out output/result` |
| `--outdir` | 多图或多 provider 输出目录 | `--outdir output/compare` |
| `--size` | image2 像素尺寸 | `1024x1024`、`1536x1024` |
| `--quality` | image2 质量或 banana 文生图分辨率 | `high`、`2K`、`4K` |
| `--aspect-ratio` | banana 文生图宽高比 | `1:1`、`9:16`、`16:9` |
| `--output-format` | GPT Image 输出格式 | `png`、`jpeg`、`webp` |
| `--output-compression` | JPEG/WebP 压缩质量 | `85` |
| `--background` | GPT Image 背景模式 | `auto`、`opaque` |
| `--n` | 请求图片数量 | `1`–`10` |
| `--timeout` | 请求超时秒数 | `300` |
| `--dry-run` | 打印最终请求，不联网 | — |
| `--debug` | 打印请求摘要和响应结构 | — |
| `--no-proxy` | 忽略系统代理，强制直连 | — |
| `--param` | 中转站私有请求字段，可重复 | `--param seed=42` |

查看全部参数：

```bash
python3 genimg.py --help
```

## 9. 自定义图片 provider

只有需要修改模型、接口或添加其他 provider 时，才复制配置文件：

```bash
cp providers.example.json providers.json
```

内置配置结构：

```json
{
  "banana": {
    "base_url": "https://你的中转站/v1",
    "api_key_env": "GENIMG_API_KEY",
    "mode": "images",
    "model": "gemini-3-pro-image",
    "endpoint": "/images/generations",
    "edit_mode": "chat",
    "edit_endpoint": "/chat/completions",
    "defaults": {
      "aspect_ratio": "1:1",
      "quality": "1K",
      "n": 1
    }
  },
  "image2": {
    "base_url": "https://你的中转站/v1",
    "api_key_env": "GENIMG_API_KEY",
    "mode": "images",
    "model": "gpt-image-2",
    "endpoint": "/images/generations",
    "edit_mode": "images",
    "edit_endpoint": "/images/edits",
    "defaults": {
      "size": "1024x1024",
      "quality": "high",
      "output_format": "png",
      "n": 1
    }
  }
}
```

字段含义：

- `mode` / `endpoint`：文生图使用的协议和路径。
- `edit_mode` / `edit_endpoint`：出现 `--image` 时使用的编辑协议和路径。
- `api_key_env`：读取 API key 的环境变量名称。
- `defaults`：该 provider 默认携带的请求参数。
- `extra_body`：中转站要求的额外 JSON 请求字段。

参数优先级：

```text
--param > 命令行结构化参数 > defaults > provider 顶层旧式参数 > extra_body > 脚本默认值
```

自定义 provider 接口不一致时，可以覆盖底层参数：

```bash
python3 genimg.py "海报" --provider image2 \
  --param seed=42 \
  --param vendor.image_config.foo=true
```

## 10. 常见问题

### 提示缺少 base_url

确认当前终端已经加载环境变量：

```bash
echo "$IMAGE_API_BASE"
```

macOS 配置过但当前终端为空时：

```bash
source "$HOME/.genimg-env.zsh"
```

### `model_not_found` 或“无可用渠道”

中转站没有为该模型开通渠道，或模型名不一致。用 `--debug` 查看完整错误，并在中转站后台确认：

- banana 是否提供 `gemini-3-pro-image`
- image2 是否提供 `gpt-image-2`

脚本不会在失败时自动偷换 provider。

### banana 编辑提示 quality / aspect_ratio 被忽略

这是预期行为。banana 文生图走 Images 接口，可以使用这些参数；banana 图片编辑走 Chat 多模态接口，没有统一的尺寸/质量字段映射。

### HTTP 502、连接失败或超时

先把超时提高到 300 秒：

```bash
python3 genimg.py "测试图" --provider banana --timeout 300
```

如果系统代理拦截请求，再尝试：

```bash
python3 genimg.py "测试图" --provider banana --timeout 300 --no-proxy
```

### 返回成功但没有保存图片

运行：

```bash
python3 genimg.py "测试图" --provider image2 --debug
```

检查返回是 `b64_json`、图片 URL、Markdown 图片还是 Gemini `inlineData`。脚本会自动解析常见格式；未知中转站格式需要调整 provider 的 `mode` 或响应解析。

### Cloudflare Error 1010

更新到最新脚本。JSON 请求使用浏览器 User-Agent，multipart 编辑请求使用与 curl 兼容的 User-Agent，以兼容常见中转站和 WAF。

## 安全说明

- 不要把 API key 写入 `providers.json`、README、命令历史或 Git。
- macOS 优先使用 `configure-macos.sh` 将 key 存入 Keychain。
- `--debug` 会截断图片 base64，但仍不建议把完整调试日志公开发布。
- `--api-key` 会让 key 出现在命令历史中，只建议临时排障。

## License

MIT
