# Image-Gen

一个可直接安装为 Agent Skill 的 Sub2API 图片与视频工具。仅依赖 Python 3 标准库，
支持 Windows、macOS 和 Linux。

功能：

- 模型自动发现：启动查 `/v1/models`，自动挑账号真正能用的生图模型，绝不因白名单误拦。
- Gemini Flash/Pro、GPT Image 及中转上新模型（2.5-flare、grok-imagine 等）文字生图。
- 本地文件、图片 URL、Data URL、Base64 图片编辑。
- 多图参考与遮罩编辑（遮罩尺寸自动校验）。
- 多提示词批量生成、多 provider 并发对比。
- 每次运行落一份 JSON 清单：参数、路径、宽高、格式、sha256、耗时。
- 即梦/Seedance 异步视频创建、续查、下载，任务 ID 自动持久化。
- 有 `ffprobe` 时自动校验视频分辨率与时长。
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
`providers.json`。macOS 用户也可以运行 `bash configure-macos.sh`，将 Key 保存到 Keychain。

### Windows 注意

- 命令里的 `python3` 一律换成 `python`（Windows 上通常没有 `python3` 这个命令）。
- 终端显示中文或 `✓ ✗ ⚠` 乱码时，先执行 `chcp 65001`，或改用 Windows Terminal / PowerShell 7。
- 三个脚本启动时会把 stdout/stderr 统一成 UTF-8 并对无法编码的字符降级替换，
  **不会再因控制台代码页抛 `UnicodeEncodeError` 中断**（此前英文 Windows 会直接崩）。
- `install.ps1` 会自动把控制台输出编码设为 UTF-8。
- 路径反斜杠、正斜杠都可以，输出目录自动创建；`ffprobe`（视频校验，可选）需自行安装并加入 PATH。

先自检。注意 `--dry-run` 不联网，验证不了 Key；要验证 Key 用 `--list-models`（免费）：

```bash
python3 genimg.py --list-models      # 能看到模型列表 = 地址和 Key 都对
python3 genimg.py "x" --dry-run      # 只看请求体，不联网
python3 genvideo.py "x" --dry-run    # 同上
```

出第一张图：

```bash
python3 genimg.py "一只戴墨镜的柴犬" --size 1024x1024 --out ./first.png
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

# 两种模型同时生成，方便比较（并行）
python3 genimg.py "赛博朋克城市夜景" --provider banana,image2 --jobs 2

# 多提示词批量：一行一条，# 开头是注释
python3 genimg.py --prompt-file prompts.txt --provider banana
```

每次真实运行都会在输出目录写 `run-<时间戳>.json`：提示词、模型、最终参数、每张图的
绝对路径、宽高、格式、字节数、sha256 前 16 位与耗时。`--no-manifest` 关闭。

模型发现与择优：

```bash
# 看账号能用哪些生图模型（免费，不生成图片）
python3 genimg.py --list-models

# 不指定模型：自动打 /v1/models 择优
python3 genimg.py "提示词" --provider banana

# 显式指定，任意模型名都放行（可用性由中转判断）
python3 genimg.py "提示词" --model gpt-image-2.5-flare

# 终端里交互挑选
python3 genimg.py "提示词" --pick-model

# 忽略缓存 / 完全跳过查询
python3 genimg.py "提示词" --refresh-models
python3 genimg.py "提示词" --no-discover
```

择优顺序：配置值（若账号可用）→ `gpt-image-2` → `gpt-image-gemini-flash` → 列表里
第一个像生图的模型。Pro 级不参与自动选择。列表缓存在 `output/.genimg-models.json`，
TTL 10 分钟；拉取失败会回退配置默认，不阻塞出图。

provider 只是「默认参数包」：

| provider | 默认模型 | 默认参数 | 生成/编辑接口 |
|---|---|---|---|
| `banana` | `gpt-image-gemini-flash` | `n=1` | `/images/generations`、`/images/edits` |
| `image2` | `gpt-image-2` | `size/quality/output_format/n` | 同上 |

已知上游名（`gemini-3-pro-image` 等）会自动翻成中转别名。不要改用 `/chat/completions`。

## 生图参数怎么设

参数分两类：能可靠改变产物的（`--size`、`--n`、`--model`），和可能被中转忽略的
（`--quality`、`--aspect-ratio`、`--background`、`--output-format`）。设置按四步走。

### 第一步：按用途选尺寸

比例只能靠 `--size` 表达（`--aspect-ratio` 实测无效）：

| 用途 | 参数 | 体积 | 耗时 |
|---|---|---|---|
| 头像 / 图标 / 方图 | `--size 1024x1024` | ~2.7 MB | ~30s |
| 草稿 / 小图 / 省流量 | `--size 512x512` | ~0.7 MB | ~35s |
| 横版封面 / Banner / 视频封面 | `--size 1536x1024` | ~2.2 MB | ~30s |
| 竖版海报 / 手机壁纸 | `--size 1024x1536` | ~2.4 MB | ~27s |
| 印刷 / 大图 / 细节展示 | `--size 2048x2048` | ~9.4 MB | ~36s |

不给 `--size` 时尺寸由模型自己定，实测会漂成 1254×1254、1230×1278、1086×1448 这类值。
要可控尺寸必须显式指定。

### 第二步：套场景配方

```bash
# 头像 / 方图
python3 genimg.py "提示词" --provider banana --size 1024x1024

# 横版封面
python3 genimg.py "提示词" --provider banana --size 1536x1024

# 竖版海报
python3 genimg.py "提示词" --provider banana --size 1024x1536

# 三个方案挑选
python3 genimg.py "提示词" --provider banana --n 3 --size 1024x1024

# 两个模型并行对比
python3 genimg.py "提示词" --provider banana,image2 --jobs 2 --size 1024x1024

# 改图并锁尺寸
python3 genimg.py "只把背景换成夜景，主体不变" --image in.png --size 1024x1024 --out out.png
```

### 第三步：看参数有没有落地

程序对比「请求 vs 实际产物」，不符就打印 ⚠ 并写进清单 `parameter_warnings`：

```text
[image2] ⚠ 请求 size=1024x1024，实际 1254x1254：中转没有按 size 出图
[image2] ⚠ 请求 background=transparent，实际是无 alpha 通道的 RGB：中转不支持透明底
[image2] ⚠ 请求 output_format=webp，实际 image/png：中转忽略了输出格式
[image2] ⚠ quality=low 无法从产物验证；实测部分中转直接忽略该参数
```

### 第四步：没生效就用替代方案

| 想要 | 中转不给 | 替代 |
|---|---|---|
| 精确比例 | `--aspect-ratio` 无效 | 用 `--size` 给宽高 |
| 透明底 | 返回无 alpha 的 RGB | 先试一次，没生效就本地抠图 |
| webp / 小体积 | 仍返回 PNG | 本地转格式 |
| 更高画质 | `--quality` 无效 | 换模型（`gpt-image-2.5-flare` 等） |
| 超过 2048 | 未验证 | 先出 2048×2048 再本地放大 |

```bash
# 转格式（macOS 自带 sips）
sips -s format jpeg -s formatOptions 80 in.png --out out.jpg

# 抠背景（有 ImageMagick 时）
magick in.png -fuzz 8% -transparent white out.png
```

### 参数实测有效性（gpt-image-2，16 张单变量对照）

| 参数 | 生效 | 说明 |
|---|---|---|
| `--size` | ✅ | 512²/1024²/1536×1024/1024×1536/2048² 均精确生效 |
| `--n` | ✅ | 真出多张，自动加序号 |
| `--quality` | ❌ | low/medium/high/4K 文件大小与画质均无差别 |
| `--aspect-ratio` | ❌ | 请求 16:9 返回 1024×1536 竖图 |
| `--background transparent` | ❌ | 返回无 alpha 的 RGB，透明像素 0% |
| `--output-format webp` | ❌ | 仍返回 PNG |
| 不给 `--size` | — | 尺寸随提示词漂移，不可控 |

体积约等于面积：512²≈0.7 MB、1024²≈2.7 MB、2048²≈9.4 MB。唯一可靠的体积杠杆是
`--size`；`--quality` 与 `--output-compression` 在这个中转上空转，耗时与参数无关
（26–49s 全在抖）。

### 不同模型的实测差异

- 有的模型忽略 `--size`（请求 1024×1024 返回 1254×1254），以清单实际宽高为准。
- 参数容忍度不同：新模型若报 `Invalid request`，换 `--provider banana` 的最小参数重试。
- 部分模型的产物链接由上游直出（如 `imgen.x.ai`），中转未代理，本机下载会 403。
  这类模型取不到图；程序只在**同源域名**下才带 Authorization，绝不把 Key 发给第三方。

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

# 自动续查本地记录里最近一个未完成任务
python3 genvideo.py --resume-last

# 列出本地建过的任务（不联网）
python3 genvideo.py --list-tasks
```

任务创建成功后会把 `task_id`、模型、时长、提示词写进输出目录的
`.genvideo-tasks.jsonl`。中断、超时或换会话后用它续查，不要重复创建付费任务。
有 `ffprobe` 时下载完会打印实际分辨率、时长和编码。

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
  --prompt-file       批量提示词文件，一行一条
  --image/-i          编辑输入，可重复
  --mask              遮罩，尺寸须与第一张输入图一致
  --model/-m          显式模型
  --size              1024x1024、1536x1024 等
  --quality           low、medium、high、1K、2K、4K 等
  --aspect-ratio      1:1、16:9、9:16 等
  --output-format     png、jpeg、webp
  --output-compression JPEG/WEBP 压缩质量 0-100
  --background        auto、transparent、opaque
  --n                 图片数量
  --list-models       查询账号可用的生图模型
  --pick-model        终端交互选择模型
  --refresh-models    忽略缓存的模型列表
  --no-discover       跳过 /models 查询，固定用配置默认
  --jobs              多 provider 并发数
  --out/-o            输出路径
  --param             key=value 覆盖任意请求字段，可 a.b=value
  --no-manifest       不写运行清单
  --dry-run           只检查请求
  --debug             去敏后显示响应结构

genvideo.py:
  --model             video-ds-2.0-fast 或 video-ds-2.0
  --seconds           视频时长
  --aspect-ratio      视频比例
  --image/--video/--audio  参考素材，可重复
  --task-id           续查任务
  --resume-last       续查本地最近未完成任务
  --list-tasks        列出本地任务记录
  --log-file          自定义任务记录路径
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
- `model_not_found`：该中转没有开通这个模型，换 `--provider banana` 或确认渠道。
- 遮罩尺寸报错：遮罩必须与第一张输入图同宽高，且带 alpha 通道。
- 新模型 `Invalid request`：参数不兼容，换 `--provider banana` 的最小参数。
- 产物下载 403（host 是上游域名）：中转没代理该产物，换产物经中转代理的模型。
- 保存失败提示「不是图片」：返回的是错误页或防盗链内容，脚本不会写成假文件。
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

测试分三层，全部不需要真实 Key：

- `tests/test_genimg.py`、`tests/test_genvideo.py`：参数构建、尺寸探测、去重、重试、
  任务记录、媒体探测等单元行为。
- `tests/test_e2e_cli.py`：用 `tests/mock_relay.py` 起本地假中转，真跑命令行，覆盖
  文生图、尺寸/数量、multipart 编辑、遮罩尺寸拦截、错误提示、非图片响应、批量提示词、
  并发、模型自动择优（假中转的列表里故意没有配置默认模型）、`--list-models`、
  `--no-discover`、模型列表缓存、视频创建/轮询/下载/续查。

也可以手动起假中转调试：

```bash
python3 tests/mock_relay.py --port 18080
IMAGE_API_BASE=http://127.0.0.1:18080/v1 GENIMG_API_KEY=sk-mock \
  python3 genimg.py "本地联调" --out ./output/mock.png
```

## License

MIT
