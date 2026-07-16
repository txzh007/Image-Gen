# genimg — 通用生图 Skill

支持多中转站/多分组的图片生成 CLI，适配 **Claude Code / Codex / OpenCode** 等 AI agent。

## 特性

- ✅ **零依赖**：纯 Python 3 标准库，无需 `pip install`
- ✅ **多中转站**：同时调用 banana、image2 等多个分组，方便对比效果
- ✅ **格式无关**：自动识别 OpenAI、Gemini、自定义中转站等各种响应格式
- ✅ **支持图生图**：传入图片进行编辑/风格转换（chat/gemini 模式）
- ✅ **调试友好**：`--debug` 模式打印原始返回，快速定位接口问题

## 快速开始

### 1. 安装

**方式 A：作为 Claude Code / OpenCode skill**

```bash
# 克隆到 skills 目录
cd .claude/skills/        # 或 .opencode/skills/
git clone <本仓库URL> genimg

# 或手动下载解压到该目录
```

**方式 B：作为独立 CLI**

```bash
# 克隆到任意目录
git clone <本仓库URL>
cd genimg
```

**方式 C：Codex 集成**

项目根目录添加 `AGENTS.md`（或追加到已有文件）：

```markdown
## 生图能力
需要生成/编辑图片时，参考 genimg/SKILL.md 运行 genimg.py。
```

### 2. 配置

**最简配置（只需一个环境变量）：**

```bash
# 设置 API Key（所有 provider 共用）
export GENIMG_API_KEY="sk-xxx"

# Windows PowerShell:
$env:GENIMG_API_KEY="sk-xxx"

# Windows CMD:
set GENIMG_API_KEY=sk-xxx
```

**可选：创建配置文件（自定义中转站/模型）**

```bash
# 复制配置模板
cp providers.example.json providers.json

# 编辑 providers.json，填入你的中转站信息：
# - base_url: 中转站地址（可选，不填则使用官方或默认地址）
# - model: 模型名
# - mode: chat | images | gemini
```

**配置示例：**

```json
{
  "image2": {
    "base_url": "https://你的中转站/v1",
    "api_key_env": "GENIMG_API_KEY",
    "mode": "images",
    "model": "gpt-image-2"
  }
}
```

**不确定接口格式？** 先跑一次 debug 看真实返回：

```bash
python genimg.py "test" --provider image2 --debug
```

根据输出选择 `mode`：
- 看到 `b64_json` / `url` 字段 → `"mode": "images"`
- 看到 `choices[].message.content` → `"mode": "chat"`
- 看到 `inlineData` → `"mode": "gemini"`

### 3. 使用

**直接调用：**

```bash
# 单个分组出图
python genimg.py "一只戴墨镜的柴犬" --provider banana

# 多个分组同时出图对比
python genimg.py "赛博朋克城市夜景" --provider banana,image2

# 图生图 / 风格转换
python genimg.py "把它变成水彩风格" --provider banana --image input.png

# 指定输出路径
python genimg.py "logo设计" --provider banana --out ./assets/logo.png

# 4K 高清生图（需中转站支持）
python genimg.py "宣传海报" --provider image2 --quality 4K

# 自定义宽高比
python genimg.py "人物肖像" --provider gemini --aspect-ratio 9:16 --quality 4K

# 指定尺寸（OpenAI 标准）
python genimg.py "风景照" --provider image2 --size 1792x1024 --quality hd
```

**高级参数：**

| 参数 | 说明 | 示例值 |
|------|------|--------|
| `--quality` | 图片质量 | `hd`（OpenAI 标准）、`4K`、`2K`（中转站扩展） |
| `--size` | 图片尺寸 | `1024x1024`、`1792x1024`、`1024x1792` |
| `--aspect-ratio` | 宽高比（需中转站支持） | `9:16`、`16:9`、`1:1` |
| `--response-format` | 返回格式 | `url`、`b64_json` |
| `--no-proxy` | 禁用代理（本地中转站必加） | - |

**性能参考：**
- 标准 1024x1024：约 60 秒
- 4K 质量：约 60-160 秒（模型和中转站而异）
- Gemini 模型：通常 15-30 秒

**通过 Agent 调用：**

安装为 skill 后，直接对话：
- "帮我画一只柴犬"
- "生成一张赛博朋克风格的城市夜景"
- "把这张图片转成水彩风格"

Agent 会自动调用脚本并返回图片路径。

## 参数说明

```
python genimg.py "提示词" [选项]

--provider, -p    分组名，逗号分隔多个，或 all
--image, -i       输入图片路径（可多次，用于图生图）
--mode            覆盖请求模式：chat | images | gemini
--model, -m       覆盖模型名
--base-url        覆盖中转站地址
--out, -o         指定输出文件名（单图时）
--outdir          输出目录，默认 ./output
--size            图片尺寸，如 1024x1024
--debug           打印原始返回，调试用
--no-proxy        绕过系统代理直连（本地中转站/代理拦截时用）
--list            列出已配置的 provider
```

## 多分组配置示例

`providers.json`：

```json
{
  "banana": {
    "base_url": "https://your-relay.com/v1",
    "api_key_env": "BANANA_API_KEY",
    "mode": "chat",
    "model": "gpt-image-1"
  },
  "image2": {
    "base_url": "https://your-relay.com/v1",
    "api_key_env": "IMAGE2_API_KEY",
    "mode": "images",
    "model": "dall-e-3"
  },
  "gemini": {
    "base_url": "https://generativelanguage.googleapis.com",
    "api_key_env": "GEMINI_API_KEY",
    "mode": "gemini",
    "model": "gemini-2.5-flash-image",
    "endpoint": "/v1beta/models/{model}:generateContent"
  }
}
```

## 常见问题

**Q: Cloudflare Error 1010 "browser signature banned"？**  
A: 中转站封了 Python 默认 User-Agent。本脚本已内置浏览器 UA 伪装，更新到最新版即可。

**Q: 返回 HTTP 502 / 连接失败？**  
A: 检查是否有全局代理（Clash/V2Ray）拦截了请求，加 `--no-proxy` 试试。

**Q: "model_not_found" 或 "无可用渠道"？**  
A: 模型名不对，或中转站该分组没配置渠道。加 `--debug` 看详细错误，去后台确认可用模型列表。

**Q: 提示找不到图片？**  
A: 加 `--debug` 看原始返回结构，可能需要调整 `mode` 设置（chat/images/gemini）。

**Q: Windows 上 `python` 命令无效？**  
A: 如果是 Microsoft Store 版本（退出码 49），找真实安装路径如 `C:\Python311\python.exe`。

**Q: 如何支持新的中转站？**  
A: 在 `providers.json` 加一项，跑一次 `--debug` 确认格式，调整 `mode` 和 `model` 即可。

## 技术细节

- **万能响应解析**：递归扫描 JSON，提取 `b64_json`、`url`、`image_url`、`inlineData`、markdown 图链、data-uri 等所有常见格式
- **格式嗅探**：自动识别 PNG/JPG/WEBP/GIF 并匹配正确扩展名
- **代理感知**：默认继承系统代理（`HTTP_PROXY`），支持 `--no-proxy` 强制直连
- **多模式请求**：chat 用 `/v1/chat/completions`、images 用 `/v1/images/generations`、gemini 用原生 API

## License

MIT

## 贡献

欢迎提 Issue / PR，特别是新中转站格式的适配案例。
