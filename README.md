# Image-Gen

一个可直接安装为 Agent Skill 的 Sub2API 图片与视频工具。仅依赖 Python 3 标准库，
支持 Windows、macOS 和 Linux。

功能：

- Gemini Flash/Pro 与 GPT Image 文字生图。
- 本地文件、图片 URL、Data URL、Base64 图片编辑。
- 多图参考与遮罩编辑。
- 即梦/Seedance 异步视频创建、续查和下载。
- 可选的本地视频素材图床上传。

所有模型请求都经过你的 Sub2 中转。Skill 只需要 Sub2 用户 Key，不接触上游 Key。

## 要求

- Python 3.8+
- 一个可用的 Sub2API 地址和用户 Key
- 无需安装 pip 依赖

## 安装

直接克隆后即可运行：

```bash
git clone https://github.com/txzh007/Image-Gen.git
cd Image-Gen
python3 genimg.py --help
```

安装到 Codex、Claude Code 或 OpenCode：

```bash
GENIMG_REPO="https://github.com/txzh007/Image-Gen.git" bash install.sh
```

Windows PowerShell：

```powershell
$env:GENIMG_REPO="https://github.com/txzh007/Image-Gen.git"
.\install.ps1
```

也可以把整个目录复制到以下任一位置：

- `~/.codex/skills/genimg`
- `~/.claude/skills/genimg`
- `~/.opencode/skills/genimg`

## 配置

macOS / Linux：

```bash
export IMAGE_API_BASE="https://你的中转站/v1"
export GENIMG_API_KEY="sk-xxx"
```

Windows PowerShell：

```powershell
$env:IMAGE_API_BASE="https://你的中转站/v1"
$env:GENIMG_API_KEY="sk-xxx"
```

`IMAGE_API_BASE` 必须以 `/v1` 结尾。不要把 Key 写进仓库或
`providers.json`。macOS 用户也可以运行 `bash configure-macos.sh`，将 Key 保存到
Keychain。

先执行不会联网和扣费的检查：

```bash
python3 genimg.py "配置检查" --provider banana --dry-run
python3 genvideo.py "配置检查" --dry-run
```

## 图片生成

```bash
# 默认 Gemini Flash
python3 genimg.py "一只戴墨镜的柴犬" --provider banana

# 明确使用 Gemini Pro
python3 genimg.py "复杂商业海报" --provider banana \
  --model gpt-image-gemini-pro

# GPT Image
python3 genimg.py "透明底产品图" --provider image2 \
  --size 1536x1024 --quality high --background transparent

# 两种模型同时生成，方便比较
python3 genimg.py "赛博朋克城市夜景" --provider banana,image2
```

模型路由固定为：

| provider | 默认模型 | 生成接口 | 编辑接口 |
|---|---|---|---|
| `banana` | `gpt-image-gemini-flash` | `/images/generations` | `/images/edits` |
| `image2` | `gpt-image-2` | `/images/generations` | `/images/edits` |

不要把真实 Gemini 上游模型名传给 Sub2，也不要改用 `/chat/completions`。

## 图片编辑

```bash
# 本地图片
python3 genimg.py "只把背景改成夜晚城市，保持主体和构图不变" \
  --provider banana --image ./input.png --out ./output/edited

# 公网图片 URL
python3 genimg.py "改成冬天下雪" --provider banana \
  --image "https://example.com/input.jpg"

# 多图参考
python3 genimg.py "保留第一张主体，采用第二张配色" \
  --provider image2 --image ./subject.png --image ./style.png

# 遮罩编辑
python3 genimg.py "只在透明区域添加足球" --provider image2 \
  --image ./input.png --mask ./mask.png
```

`--image` 支持：

- 本地文件路径
- HTTP(S) 图片 URL
- `data:image/...;base64,...` Data URL
- 纯 Base64

URL 会先下载，Base64 会先解码；脚本校验图片签名后，以内存 multipart 文件上传到
Sub2。不会把 URL 或 Base64 当成普通文本字段发送。单张输入最大 20 MB。

## 视频任务

```bash
# 默认快速模型，创建后自动轮询并下载
python3 genvideo.py "雨夜城市上空飞行，镜头缓慢推进" \
  --model video-ds-2.0-fast --seconds 5 --aspect-ratio 16:9 \
  --out ./output/result.mp4

# 只创建任务
python3 genvideo.py "海边日出" --seconds 10 --no-wait

# 续查已有任务
python3 genvideo.py --task-id task_xxx --out ./output/resumed.mp4
```

本地参考素材必须先上传为公网 URL。只有确认素材可以公开上传后，才使用：

```bash
python3 genvideo.py "让参考图片动起来" \
  --image ./input.png --auto-upload catbox
```

支持 `catbox`、`telegraph`、`smms` 和 `imgbb`。其中 `imgbb` 需要
`IMGBB_API_KEY`。也可以单独运行 `python3 upload.py --help`。

## 常用参数

```text
genimg.py:
  --provider/-p       banana、image2 或逗号分隔组合
  --image/-i          编辑输入，可重复
  --mask              遮罩
  --model/-m          显式模型
  --size              1024x1024、1536x1024 等
  --quality           low、medium、high、1K、2K、4K 等
  --aspect-ratio      1:1、16:9、9:16 等
  --n                 图片数量
  --out/-o            输出路径
  --dry-run           只检查请求
  --debug             去敏后显示响应结构

genvideo.py:
  --model             video-ds-2.0-fast 或 video-ds-2.0
  --seconds           视频时长
  --aspect-ratio      视频比例
  --image/--video/--audio  参考素材，可重复
  --task-id           续查任务
  --no-wait           创建后立即返回
  --out/-o            MP4 输出路径
```

完整参数请运行：

```bash
python3 genimg.py --help
python3 genvideo.py --help
```

## 常见错误

- `No available compatible accounts`：中转当前没有可调度渠道，稍后重试或检查后台。
- HTTP 429/503：图片脚本按 2、4、8 秒重试，保持原模型，不会自动切换收费模型。
- `images endpoint requires an image model`：检查是否误用了真实 Gemini 上游模型名。
- URL 图片失败：确认匿名服务器请求能直接返回图片内容，而不是登录页或防盗链 403。
- 视频等待中断：保存任务 ID，使用 `--task-id` 续查，不要重新创建付费任务。

## 安全

- 地址和 Key 只从 `IMAGE_API_BASE`、`GENIMG_API_KEY` 读取。
- 禁止直连上游或保存上游 Key。
- 调试输出会隐藏完整 Key、Base64 和 Data URL。
- 不要未经用户同意把本地视频素材上传到公共图床。
- 不要把 Sub2 Authorization 头转发到第三方对象存储 URL。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
