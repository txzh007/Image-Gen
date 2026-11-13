---
name: genimg
description: 通过用户自备的 OpenAI Images 兼容中转（IMAGE_API_BASE + GENIMG_API_KEY）生成和编辑图片，并创建、续查异步视频任务。用户提出生图、画图、出图、改图、修图、换背景、加文字、扩图、做海报/头像/封面/商品图/透明底、文生图、图生图、参考图风格迁移、Nano Banana、Gemini 图片、GPT Image、image2，或要求即梦、Seedance、文生视频、图生视频、视频续作时使用。
---

# genimg

三个零依赖 Python 3 脚本，全部通过用户自己的 Sub2 中转调用，不接触上游 Key：

| 脚本 | 用途 |
|---|---|
| `genimg.py` | 文字生图、图片编辑、多图参考、遮罩局部重绘、批量提示词 |
| `genvideo.py` | 创建、轮询、续查、下载异步视频任务 |
| `upload.py` | 把本地素材传到第三方图床（需用户同意） |

调用时用 Skill 目录里的**脚本绝对路径**：下文所有 `genimg.py` / `genvideo.py` 都应理解为
`<本文件所在目录>/genimg.py`，不要假设 cwd 就是 Skill 目录；输出路径指向用户当前工作目录
或用户指定位置。

## 先确认能不能用（30 秒，免费）

刚接触这个 skill、或换了中转/Key 时，先跑这一条再谈生图：

```bash
python3 "<本 skill 目录>/genimg.py" --list-models
```

它只查 `/v1/models`，**不生成图片、不产生费用**。按输出判断：

| 输出 | 含义 | 下一步 |
|---|---|---|
| 列出若干生图模型（含 `← 已知`） | 完全可用 | 直接进图片工作流 |
| `未设置 IMAGE_API_BASE…` | 没配中转地址 | 见下方「配置」，让用户提供地址与 Key |
| `拉取模型列表失败：HTTP 401/403` | Key 无效或不属于该中转 | 让用户换 Key，不要反复重试 |
| `拉取模型列表失败：HTTP 404` | 该中转没有 /models 端点 | 不阻塞出图：跳过自动择优，用 `--no-discover --model NAME` |
| 其它网络错误 | 中转不可达或被代理拦截 | 检查地址，必要时 `--no-proxy` |

拿不到模型列表也不必停：生图主链路不依赖它，`--no-discover` 会用配置默认模型直接发。

## 配置

只从环境变量读取中转地址与用户 Key：

```bash
export IMAGE_API_BASE="https://你的中转站/v1"   # 必须以 /v1 结尾
export GENIMG_API_KEY="sk-xxx"
```

不读取配置文件里的地址/Key，不读取上游 Key，不使用 `--base-url`、`--api-key` 绕开
中转（`genvideo.py` 传这两个参数会直接报错退出）。

**这两个变量缺任何一个都出不了图。缺的时候直接向用户索要「中转地址 + Key」这两项，不要
猜测、不要跳过、不要改用其它接口或直连上游。** 拿到后写进用户的 shell 配置
（zsh → `~/.zshrc`，bash → `~/.bashrc`，Windows → `$PROFILE`）并在当前会话 export，
这样用户下次不用再给；配置文件不可写时就在每条命令前 export，并提醒用户自行持久化。

**Windows**：把示例里的 `python3` 换成 `python`；终端显示乱码时先 `chcp 65001` 或用
Windows Terminal。三个脚本已统一 stdout/stderr 为 UTF-8 并对不可编码字符降级，不会因代码页
中断，可以放心跑。

自检分两种，别混：`--dry-run` **不联网**，只能检查参数拼写，验证不了 Key；要验证 Key 必须
用 `--list-models`（免费）。

```bash
python3 genimg.py --list-models            # 真验证：能看到模型列表 = 地址和 Key 都对
python3 genimg.py "x" --dry-run                       # 只看请求体，不联网
python3 genvideo.py "配置检查" --dry-run              # 同上
```

## 30 秒决策表

| 用户说 | 动作 |
|---|---|
| 生图 / 画一张 / 出图 | `genimg.py "提示词"`（默认 image2 / `gpt-image-2`） |
| 要更好效果 / 复杂海报 / 细节多 | 先 `--list-models`，再 `--model` 指定更强模型（如 `gpt-image-2.5-flare`）；Pro 必须显式加 |
| 点名 GPT Image / image2 / `gpt-image-2` | `--provider image2` |
| 想知道哪个模型更好 | `--provider image2,banana --jobs 2`（或直接用 `--model` 指定任意模型） |
| 改这张图 / 换背景 / 修图 | `--image 路径` + 只描述要改的那一处 |
| 只改局部 | `--image` + `--mask`（遮罩须与第一张图同尺寸、带 alpha） |
| 给两张参考图融合 | `--image a.png --image b.png`，用「第一张/第二张」指代 |
| 要透明底商品图 | `--background transparent`，出图后看有没有 ⚠ 再决定怎么交付 |
| 要竖版 / 横版 | `--size 1024x1536` / `--size 1536x1024`（`--aspect-ratio` 实测无效） |
| 一次生成一批图 | `--prompt-file prompts.txt`（一行一条） |
| 要 N 张候选 | `--n 3` |
| 不确定账号能用哪些模型 | `--list-models`（免费，不生成图） |
| 想让人从列表里挑模型 | `--pick-model`（终端交互，agent 环境自动跳过） |
| 要做视频 / 即梦 / Seedance | `genvideo.py`，见下方视频工作流 |

## 图片工作流

### 模型：自动发现 + 自动择优

provider 只是本地的「默认参数包」，真正发给中转的是模型名。**默认 provider 是 `image2`
（真实模型 `gpt-image-2`）；`banana` 是 `gpt-banana` 历史兼容别名，不是真实模型**，仅当账号
里确实有 `gpt-image-gemini-*` 时才用。启动时会先查一次
`/v1/models`（结果缓存在 `output/.genimg-models.json`，TTL 10 分钟），据此决定用哪个
模型：

| 情况 | 行为 |
|---|---|
| 用户没指定模型 | 自动择优：配置值（若账号有）→ `gpt-image-2` → `gpt-image-gemini-flash` → 列表里第一个像图片的模型；切换会打印说明 |
| 用户 `--model NAME` 指定 | 直接发该模型；不在已知列表只打印一行提示，不再本地拦截 |
| 拉不到列表（网络/权限） | 回退到配置默认模型，继续出图，不阻塞 |
| `--no-discover` | 跳过查询，固定用配置默认 |
| `--refresh-models` | 忽略缓存重新拉 |

自动择优**不会**选 `gpt-image-gemini-pro` 这类高价模型。Pro 必须用户显式加
`--model gpt-image-gemini-pro`；Pro 失败不要静默降级成 Flash。

```bash
python3 genimg.py --list-models                 # 看账号能用哪些生图模型
python3 genimg.py "提示词" --provider image2    # 自动择优
python3 genimg.py "提示词" --model gpt-image-2.5-flare   # 显式指定任意模型
```

文字生图固定 `/images/generations`，带图编辑固定 `/images/edits` multipart；不要改用
`/chat/completions`。已知上游名（`gemini-3-pro-image` 等）仍会自动翻成中转别名。

### 不同模型的兼容性（实测）

- **有的模型忽略 `--size`**：请求 1024×1024 可能返回 1254×1254。以清单里的实际宽高为准，
  汇报时说实际值，不要复述请求值。
- **参数容忍度不同**：某模型在 `image2`（会带 `quality`/`output_format`）下报
  `Invalid request`，换成不给默认参数的 `--provider banana`（只带 model/prompt/n）就能发出去。
  新模型先试 banana 组的最小参数。
- **产物可能由上游直出**：部分模型的图片链接是上游域名（如 `imgen.x.ai`）且需要上游
  鉴权，中转没代理该产物，下载必然 403。这类模型在本机取不到图，换 `gpt-image-2` 等
  产物经中转代理的模型。绝不把中转 Key 发给第三方域名。

## 生图参数设置

参数分两类：**能可靠改变产物的**（`--size`、`--n`、`--model`）和**中转可能空转的**
（`--quality`、`--aspect-ratio`、`--background`、`--output-format`）。设置参数走四步，
第 4 步不做就不许汇报。

### 第一步：从需求里抠约束

| 用户表述 | 参数 | 备注 |
|---|---|---|
| 尺寸、竖版、横版、方形、比例 | `--size WxH` | 唯一可靠的尺寸入口 |
| 几张、几个方案、要挑 | `--n N` | 自动加 `_0/_1/_2` 后缀 |
| 透明底、去背景 | `--background transparent` | 必须校验是否生效 |
| png / jpg / webp、压小一点 | `--output-format` + `--output-compression` | 必须校验是否生效 |
| 高清、2K、4K | `--size 2K` / `--size 4K` | **用 size 别名，不是 quality**；4K 实测 3840×2160 或 4096² |
| 指定或换模型 | `--model NAME` | 不指定则自动择优 |
| 种子、复现同一张 | `--param seed=42` |
| 动图 / GIF | `--output-format gif` | 需中转支持，且尺寸会压缩 | 中转支持才有效 |
| 中转私有字段 | `--param key=value` | 支持 `a.b=value` 嵌套 |

用户没提的一律用 provider 默认值：不擅自上 Pro、4K、透明底或更大尺寸。

### 第二步：把「比例」换算成具体宽高

`--aspect-ratio` 实测无效（请求 16:9 返回了竖图），比例只能通过 `--size` 表达。
实测可用档位：

| 用途 | 参数 | 实测体积 | 耗时 |
|---|---|---|---|
| 头像 / 图标 / 方图 | `--size 1024x1024` | ~2.7 MB | ~30s |
| 草稿 / 小图 / 省流量 | `--size 512x512` | ~0.7 MB | ~35s |
| 横版封面 / Banner / 视频封面 | `--size 1536x1024` | ~2.2 MB | ~30s |
| 竖版海报 / 手机壁纸 | `--size 1024x1536` | ~2.4 MB | ~27s |
| 4K / 印刷 / 大图 | `--size 4K` | 13–26 MB | 50–75s |

不给 `--size` 时尺寸由模型自己定，实测漂移出过 1254×1254、1230×1278、1086×1448、
1024×1536。需要可控尺寸就必须显式指定。

### 第三步：常见场景直接套配方

```bash
# 头像 / 方图
python3 genimg.py "提示词" --size 1024x1024

# 横版封面
python3 genimg.py "提示词" --size 1536x1024

# 竖版海报（手机屏）
python3 genimg.py "提示词" --size 1024x1536

# 要几个方案挑选
python3 genimg.py "提示词" --n 3 --size 1024x1024

# 两个模型并行各出一张对比
python3 genimg.py "提示词" --provider image2,banana --jobs 2 --size 1024x1024

# 商品透明底（必须校验）
python3 genimg.py "提示词" --size 1024x1024 --background transparent

# 改图且锁住尺寸，别让模型自己改构图
python3 genimg.py "只把背景换成夜景，主体不变" --image in.png \
  --size 1024x1024 --out out.png
```

### 第四步：校验参数有没有落地（必做）

脚本会对比「请求 vs 实际产物」，不符就打印 ⚠，并写进清单的 `parameter_warnings`：

```text
[image2] ⚠ 请求 size=1024x1024，实际 1254x1254：中转没有按 size 出图
[image2] ⚠ 请求 background=transparent，实际是无 alpha 通道的 RGB：中转不支持透明底
[image2] ⚠ 请求 output_format=webp，实际 image/png：中转忽略了输出格式
[image2] ⚠ quality=low 无法从产物验证；实测部分中转直接忽略该参数
```

看到 ⚠ 就按下面的替代方案处理，并把真实情况告诉用户，不要当作已生效。

### 参数生效性（真实中转实测：gpt-image-2，16 张单变量对照）

| 参数 | 生效 | 实测证据 |
|---|---|---|
| `--size` 512² / 1024² / 1536×1024 / 1024×1536 / 2048² | ✅ | 请求多少返回多少 |
| `--size 1K/2K/4K` | ✅ | 服务端按别名定尺寸：4K→3840×2160 或 4096²，2K→2048² |
| `--size 16:9` 等比例字符串 | ✅ | 按该比例选尺寸，如 1824×1024 / 1816×1024 |
| `--n 2` | ✅ | 真返回 2 张 |
| `--quality low / medium / high` | ❌ | 固定 size 下文件体积与画质无差别 |
| `--aspect-ratio 16:9` | ❌ | 返回 1024×**1536** 竖图 |
| `--background transparent` | ❌ | 返回 color_type=2 的 RGB，透明像素 0% |
| `--output-format webp` + `--output-compression` | ❌ | 返回的仍是 PNG |
| 不给 `--size` | ⚠ | 尺寸随提示词漂移，不可控 |

体积近似按面积走：512²≈0.7 MB、1024²≈2.7 MB、4K≈13–26 MB。注意 4K 别名返回的是**超大 b64 或 URL**，把 `--timeout` 提到 600，别用短超时硬截断。**唯一可靠的体积杠杆是
`--size`**；`--quality` 和 `--output-compression` 在这个中转上是空转。耗时与参数无关
（26–49s 全在抖）。

### 参数没生效怎么办

| 用户要的 | 中转不给 | 替代做法 |
|---|---|---|
| 精确比例 | `--aspect-ratio` 无效 | 用 `--size` 直接给宽高 |
| 透明底 | 返回无 alpha 的 RGB | 先试一次；没生效就明说，再本地抠图（下方命令） |
| webp / 更小文件 | 仍返回 PNG | 本地转格式（下方命令） |
| 画质更高 | `--quality` 无效 | 换模型（`gpt-image-2.5-flare` 等），实测质感确有差别 |
| 写死 `--size 4096x4096` | ⚠ 常见超时或静默降级 | 改用 `--size 4K` 别名，并把 `--timeout` 提到 600 |

本地收尾（优于假装参数生效）：

```bash
# 转格式 / 压体积（macOS 自带 sips）
sips -s format jpeg -s formatOptions 80 in.png --out out.jpg

# 抠掉纯色背景（有 ImageMagick 时）
magick in.png -fuzz 8% -transparent white out.png
```

### 参数设置的硬规则

- 只有 `--size`、`--n`、`--model` 能可靠改变产物；其余参数只当「请求」，不当「保证」。
- 传了 `--quality high` 不等于画质提升，传了 `--background transparent` 不等于透明底 ——
  没校验过的参数不许写进汇报。
- 用户没提的增强参数一律不加；用户明确要 4K 时用 `--size 4K`，不是 `--quality 4K`。
- 要小文件就降 `--size`，不要指望 quality 或压缩参数。

### 输入图来源

`--image` / `--mask` 接受本地路径、HTTP(S) URL、`data:image/...;base64,...`、纯 Base64，
可重复。脚本会把 URL 下载、Base64 解码、校验文件签名后，作为 multipart 文件字段上传；
单张上限 20 MB。不要把 URL 或 Base64 当普通文本字段发。

编辑时只用「改哪里、改成什么」描述动作，保留主体与构图说明；一次只改一到两处，
多轮迭代比一次写十条要求更稳。遮罩尺寸与第一张输入图不一致会被直接拒绝。

### 批量与并发

```bash
# 多提示词批量：一行一条，# 开头是注释
python3 genimg.py --prompt-file prompts.txt

# 两个模型并行出图，便于挑选
python3 genimg.py "赛博朋克街景" --provider image2,banana --jobs 2
```

### 输出与运行清单

- 默认输出目录 `./output`；`--out` 指定文件名，多图/多 provider 时自动加序号后缀，
  后缀以实际图片格式为准（请求 png 也可能拿到 jpg）。
- 每次真实运行都会写一份 `output/run-<时间戳>.json` 清单，含提示词、模型、实际参数、
  每张图的绝对路径、宽高、格式、字节数、sha256 前 16 位、耗时；`--no-manifest` 可关。
  后续追问「刚才那张参数是什么」直接读清单，不要重新生成。

## 提示词工程

默认把中文需求写成结构化描述，而不是一句话丢过去。推荐骨架：

```
主体（谁/什么，外观细节）+ 动作/姿态 + 场景环境 + 构图与机位（近景/俯拍/三分法）+
光线（侧逆光/柔光/霓虹）+ 材质与风格（胶片颗粒/等距 3D/扁平插画）+ 色彩倾向 +
画面比例与留白要求 + 明确排除项
```

要点：

1. **具体优于形容词堆叠**。「米白色粗针织毛衣」优于「好看的衣服」。
2. **文字渲染**：把要出现的文字用引号原样写出并指定位置语言字体感，例如
   `画面中央烫金字「OPEN」，无衬线粗体`；文字越短越准，长句容易糊。
3. **参考图**用「第一张/第二张」指代，并说明各自继承什么：
   `保留第一张的人物姿态与脸型，采用第二张的配色与光影`。
4. **编辑只描述差异**：`只把背景换成雨夜霓虹街道，人物、服装、姿势、构图完全不变`。
   要求「完全不变」的部分要点名，否则模型会顺手改掉。
5. **一致性系列**：同一批图固定风格词、光线词与 `--param seed=42`，只替换主体。
6. **负面约束写成正向替代**：「不要模糊」→「主体边缘锐利、细节清晰」。
7. **别写模型不知道的私有指代**（「我上次那个风格」），先落到具体视觉特征。

## 结果汇报契约

图片完成后回报：provider、模型、图片绝对路径、**实际宽高与格式**、耗时；多张就逐条列。
有 `⚠` 参数未生效时必须一并说明（例：「size 请求 1024×1024，实际返回 1254×1254」），不许只报请求值。
生成失败时回报 HTTP 状态与中转原文关键句，加上脚本给出的下一步提示，不要编造成功。

## 4K 与 size 别名（实测）

用户要 4K 时**不要用 `--quality 4K`**，那个字段实测被忽略。正确的入口是 `--size` 的别名：

```bash
python3 genimg.py "提示词" --model gpt-image-2.6-flare --size 4K --timeout 600 --out ./out.png
```

| `--size` | 实际输出 | 体积 | 耗时 |
|---|---|---|---|
| `4K` | 3840×2160 或 4096×4096（随模型/构图） | 13–26 MB | 50–75s |
| `2K` | 2048×2048 | ~9.6 MB | 160s |
| `16:9` | 1824×1024 | ~2.4 MB | ~50s |
| `1536x1024` | 1536×1024 | ~2.3 MB | 40–60s |

要点：

1. **`size` 兼容 `WIDTHxHEIGHT`、比例、`auto`、`1K/2K/4K`**；也可传 `width`/`height` 成对，
   非 `auto` 的 size 优先。写死 `4096x4096` 会超时或静默降级，用 `4K` 别名。
2. `resolution: "4K"` 配合 `size=1816x1024` 是**超分分组**的能力，由 API Key 所属分组路由；
   普通分组不认（实测输出仍 1816×1024）。有没有超分看 Key，不看模型。
3. 4K 会返回超大 `b64_json`（十几 MB）或图片 URL，`--timeout` 至少 600，
   否则会被截断成「返回不是 JSON」或「保存失败」。
4. **第三方 IP 相似性审核**：直接写「奥特曼」等角色名会被
   `content_policy_violation: 与第三方内容相似性防护限制` 拒绝；改成
   「银红配色的巨型科幻英雄」等描述性说法即可通过。

### 新参数（已内置）

| 需求 | 参数 | 发出的字段 |
|---|---|---|
| 超分分组按分辨率出图 | `--size 1816x1024 --resolution 4K` | `"size":"1816x1024","resolution":"4K"` |
| 成对宽高 | `--width 2048 --height 1152` | `"width":2048,"height":1152`（必须成对） |
| 4K 别名 | `--size 4K` | `"size":"4K"` |

`resolution` 由 **API Key 所属分组**路由：超分分组会按 `size` 的比例算出目标尺寸；普通分组
不认（实测输出仍等于请求 size）。脚本只负责把字段原样提交，不做猜测。

### 异步任务（`--async`）

```bash
python3 genimg.py "提示词" --async --size 4K --out ./out.png       # 创建 + 轮询 + 下载
python3 genimg.py "提示词" --async --no-wait                        # 只拿 task_id
python3 genimg.py --task-id TASK_ID --out ./out.png                 # 续查，不重新提交
python3 genimg.py --resume-last                                      # 续查最近未完成任务
python3 genimg.py --list-tasks                                       # 看本地任务记录
```

- 端点：`POST /images/generations/async`（编辑为 `/edits/async`）→
  `GET /images/tasks/{task_id}`，查询必须用提交时同一个 Key。
- **站点开关决定**：未启用返回 404 `async image tasks are not enabled`，脚本会提示
  「去掉 --async 走同步」；4K 实测同步 47–75s 可完成，通常不需要异步。
- 创建成功后立即把 task_id 写进输出目录 `.genimg-async-tasks.jsonl`；中断、超时、换会话
  一律 `--task-id` 续查，**绝不重复提交付费任务**。
- `--poll-interval`（默认 5s）、`--wait-timeout`（默认 900s）控制等待。

## GIF / 动图

能否出 GIF **取决于中转**：OpenAI 官方把循环 GIF 放在 ChatGPT 应用里，Images API 不保证
支持。两种实测结果都遇到过 ——

- 旧渠道：`format=gif` / `response_format=gif` 返回 HTTP 200 但产物是 **PNG**（参数被忽略）
- 新渠道：`output_format: "gif"` 返回真 **GIF89a**，多帧 + NETSCAPE 循环扩展

只能用 `output_format` 试，拿到文件后**必须验签名**（`GIF89a` 开头才算成功）：

```bash
python3 genimg.py "一个挥手的小机器人，循环动画" --output-format gif --out ./out.gif
```

实测：请求 `size 1024x1024` 时 GIF 实际只有 **412x414** —— GIF 模式会压尺寸，别按请求值
汇报。文件质量不满意就退回视频转 GIF：

```bash
python3 togif.py output/clip.mp4 --start 5 --duration 3   # 截 5s 起 3 秒
python3 togif.py output/clip.mp4 --width 480 --fps 12 --colors 96
```

- 需要系统 `ffmpeg`（`brew install ffmpeg` / `choco install ffmpeg`）
- `--fps` 12–15 足够；`--colors` 128 起，文件超 3 MB 就降到 96 并减小 `--width`
- 生成后报告实际尺寸、帧数与体积

## 视频工作流

**先确认账号有没有视频能力**（免费，不创建任务）：

```bash
python3 genvideo.py --list-models
```

返回「没有视频模型」= 该中转分组未开通视频，图片能力不受影响；不要反复重试，
直接告诉用户需要中转后台授权。返回若干 `video-*` / `seedance-*` 模型 = 可以开工。

```bash
# 默认快速版，创建后轮询并下载
python3 genvideo.py "雨夜城市上空飞行，镜头缓慢推进" \
  --model video-ds-2.0-fast --seconds 5 --aspect-ratio 16:9 \
  --out ./output/result.mp4

# 只创建任务，立刻拿 task_id
python3 genvideo.py "海边日出" --seconds 10 --no-wait

# 用任务 ID 续查（绝不重复创建付费任务）
python3 genvideo.py --task-id task_xxx --out ./output/resumed.mp4

# 从本地记录自动续查最近未完成任务
python3 genvideo.py --resume-last

# 看本地已建过哪些任务
python3 genvideo.py --list-tasks
```

默认 `video-ds-2.0-fast`；用户明确要标准版才用 `video-ds-2.0`。

### 时长与分辨率：两组字段，实测哪组有效

中转对这两个维度有两套字段写法，**不能混着发**，否则可能被忽略：

| 维度 | 写法 A（旧，默认） | 写法 B（实测有效） |
|---|---|---|
| 时长 | `--seconds 5` → `"seconds":"5"`（字符串） | `--duration 5` → `"duration":5`（**数字**） |
| 尺寸 | `--aspect-ratio 16:9` → `"aspect_ratio":"16:9"` | `--resolution 720p` → `"resolution":"720p"` |

实测的分辨率档位与计费（同一提示词、5 秒）：

| 模型 | `--resolution` | 实际输出 | 体积 | 上游 ticks |
|---|---|---|---|---|
| `grok-imagine-video` | 480p | 848×480 | 2.2 MB | 2.5e9 |
| `grok-imagine-video` | 720p | 1280×720 | 4.1 MB | 3.5e9 |
| `grok-imagine-video` | 1080p | ❌ 400 上游拒绝 | — | — |
| `grok-imagine-video-1.5` | 480p | 848×480 | 3.4 MB | 4.0e9 |
| `grok-imagine-video-1.5` | 720p | 1280×720 | 7.8 MB | 7.0e9 |
| `grok-imagine-video-1.5` | 1080p | **1920×1088** | 16 MB | 12.5e9 |

三条硬结论：

1. **1080p 只有 `-1.5` 支持**；普通版给 1080p 会被上游 400 拒掉。
2. **`--resolution` 只认 `480p`/`720p`/`1080p` 这类档位名**，写 `1920x1080` 会 422。
3. **`-1.5` 同档位贵约 2 倍，码率也高约 2 倍**（720p：7.8 MB vs 4.1 MB），画质更细；
   480p 时两者分辨率相同，只是 1.5 编码更重。

在 `grok-imagine-video` 上实测的差别是**实质性的**：

| 请求 | 实际输出 | 上游计费 ticks |
|---|---|---|
| `seconds:"5"` + `aspect_ratio:"16:9"` | 848×480 | 2,500,000,000 |
| `duration:5` + `resolution:"720p"` | **1280×720** | 3,500,000,000 |

也就是说 `aspect_ratio` 只能算"大致比例"，`resolution` 才能拿到确定分辨率。要精确分辨率、
或者要和账单对得上时，用 B 组：

```bash
python3 genvideo.py "雨夜城市上空飞行" --model grok-imagine-video \
  --duration 5 --resolution 720p --out ./output/city.mp4
```

给了 `--duration` 就不再发 `seconds`，给了 `--resolution` 就不再发 `aspect_ratio`；
没给则沿用 A 组默认值。

### 两种协议：`--protocol`

端点骨架两种协议相同（`POST /v1/videos` → `GET /v1/videos/{id}` 轮询 → `GET
/v1/videos/{id}/content` 下载 MP4），差别只在请求字段：

| | `--protocol sub2`（默认） | `--protocol openai` |
|---|---|---|
| 默认模型 | `video-ds-2.0-fast` | `sora-2` |
| 画面比例 | `aspect_ratio: "16:9"` | `size: "1280x720"`（只认 4 个尺寸） |
| 时长 | 任意，如 `5`/`10` | **只支持 4/8/12 秒** |
| 参考素材 | `images`/`videos`/`audios` 数组 | 单个 `input_reference` |
| 适用 | Sub2API、即梦/Seedance 等扩展中转 | OpenAI 官方 Sora 或严格兼容它的中转 |

```bash
# OpenAI 协议：比例自动换算成 size
python3 genvideo.py "雨夜城市上空飞行" --protocol openai --seconds 8 --aspect-ratio 16:9

# 直接给官方尺寸
python3 genvideo.py "雨夜城市" --protocol openai --size 720x1280 --seconds 4

# 图生视频（单张参考图）
python3 genvideo.py "让这张图动起来" --protocol openai \
  --image https://example.com/in.png --seconds 4
```

`openai` 协议下多张参考图、视频/音频参考、非 4/8/12 秒都会被本地直接拒绝并说明原因 ——
这些是协议限制，不是脚本限制。中转只支持其中一种协议时，换 `--protocol` 即可，不用改代码。

### 中转返回形状（实测，grok-imagine-video）

创建只返回一个 ID，没有 status：

```json
{"request_id": "4be42438-6de2-9276-8cc5-7123b04e21f7"}
```

轮询到完成时：

```json
{"model":"grok-imagine-video","progress":100,"status":"done",
 "usage":{"cost_in_usd_ticks":2500000000},
 "video":{"duration":5,"url":"/v1/videos/<id>/content"}}
```

注意两点：成功状态是 **`done`**（不是 `completed`），任务 ID 字段是 **`request_id`**。脚本
对这两类差异都已兼容，并把 `usage` 原样记进任务记录，方便和后台账单对账。

**任务记录**：创建成功后立刻把 `task_id`、模型、时长、提示词写进输出目录的
`.genvideo-tasks.jsonl`（`--log-file` 可改），完成/失败也会追加一条。中断、超时、
上下文丢失后一律 `--task-id` 或 `--resume-last` 续查，不要重新创建。

参考素材必须是公网 URL。本地文件要先征得用户同意再上传图床（`--auto-upload catbox`
或用户指定服务），未经确认不要公开上传。完成后脚本会打印保存路径与媒体信息
（有 `ffprobe` 时给出分辨率、时长、编码）。

## 错误处理

| 现象 | 处理 |
|---|---|
| HTTP 429 / 503 | 脚本已按 2/4/8 秒退避重试 3 次且不换模型；仍失败就等下再试 |
| `model_not_found` | 该中转没开通此模型；先 `--list-models` 看账号有什么，或让用户确认渠道 |
| `No available compatible accounts` | 分组暂时没有可调度渠道，稍后重试或换 provider |
| `images endpoint requires an image model` | 误用了真实 Gemini 上游模型名 |
| 余额 / 额度不足 | 如实回报，不要换模型反复试 |
| 内容审核拒绝（`upstream_policy_refusal_text` 等，可能是外语原文） | 换描述或去掉敏感词后重试一次；同一提示词不要连刷；要稳就换 `--model gpt-image-2` |
| 新模型 `Invalid request` | 参数不兼容，改用 `--provider banana`（最小参数）再试，或删掉多余的 size/quality |
| 产物下载 403（host 是上游域名） | 中转未代理该产物，客户端取不到；换产物经中转代理的模型 |
| 实际尺寸≠请求尺寸 | 某些模型忽略 `--size`，以清单实际宽高为准 |
| 413 | 减少 `--image` 张数或先压缩参考图 |
| 下载结果不是图片/MP4 | 链接过期或防盗链，重新生成或续查，不要落盘假文件 |
| 视频等待超时 | 保留 task_id，`--task-id` 续查 |
| 创建成功但日志说「没有 task_id」 | 中转的任务标识字段可能是 `task_id`/`request_id`/`video_id`/`job_id`/`id`，脚本已全部识别；若是新名字，用 `--debug` 看原始响应 |
| 视频一直「等待中」不结束 | 部分渠道成功状态是 `done` 而非 `completed`，脚本已兼容 done/succeeded/finished/ready；若仍未结束，`--list-tasks` 拿 task_id 后手动查一次状态 |

排查用 `--debug`；它已对 Key、Base64、Data URL 去敏，不要自己打印完整 Key。
下载视频时不要把中转 Authorization 头转发给第三方对象存储地址。

## 硬性约束

- 不擅自用 Pro、4K、透明底等加价或加时参数；用户没提就用默认。要 4K 时正确的入口是 `--size 4K`。
- 模型被自动切换时必须如实告知用户换成了哪个，不许悄悄换完就当作原模型的结果。
- 不在失败时静默换收费模型、静默降级质量或谎报成功。
- 不重复创建已存在的付费视频任务。
- 不把中转地址或 Key 写进仓库、配置文件或日志。
- `IMAGE_API_BASE` 必须含 `/v1`；地址与 Key 只来自环境变量。

完整参数以 `python3 genimg.py --help`、`python3 genvideo.py --help` 为准；
只有需要调整 provider 默认参数时才复制 `providers.example.json` 为 `providers.json`。
