---
name: genimg
description: 生成图片。当用户要求"生图/画图/文生图/图生图/edit image/generate image"时使用。支持多个中转站分组（如 banana、image2）同时出图。
---

# genimg — 通用生图 skill

用一个零依赖的 Python 脚本调用中转站的生图接口。适用于 Claude Code / Codex / OpenCode 等任意能跑 shell 的 agent。

## 何时用

用户想「生成/编辑图片」时。中转站的生图通常是独立计费分组，这里把每个分组抽象成一个 **provider**（如 `banana`、`image2`），可单独或同时调用。

## 首次配置（只做一次）

1. 复制 `providers.example.json` 为 `providers.json`，填入各分组的 `base_url` / `model` / `mode`。
2. 设置 API key 环境变量（名字对应配置里的 `api_key_env`）：

   ```bash
   export BANANA_API_KEY=sk-xxx
   export IMAGE2_API_KEY=sk-yyy
   ```

3. **不确定接口格式时**，先跑一次 debug 看真实返回，再定 `mode`：

   ```bash
   python genimg.py "a cat" --provider image2 --debug --no-proxy
   ```

   - 返回里是 `b64_json` / `url` → 用 `--mode images`
   - 返回像聊天消息、图片藏在 markdown 或 `image_url` 里 → 用 `--mode chat`
   - 返回里有 `inlineData` → 用 `--mode gemini`
   
   **常见问题**：遇到 Cloudflare 1010 错误或代理干扰时加 `--no-proxy`。

## 用法

```bash
# 单个分组
python genimg.py "一只戴墨镜的柴犬" --provider banana

# 同时用两个分组出图（各存一张，方便对比）
python genimg.py "赛博朋克城市夜景" --provider banana,image2

# 配置里全部分组
python genimg.py "森林里的小屋" --provider all

# 图生图 / 编辑（chat 或 gemini 模式）
python genimg.py "把它变成水彩风格" --provider banana --image input.png

# 指定输出文件名
python genimg.py "logo" --provider banana --out ./assets/logo

# 查看已配置分组
python genimg.py --list
```

图片默认存到 `./output/<provider>_<时间戳>.<ext>`。

## 给 agent 的执行提示

- 用户没指定 provider 时，默认 `banana`；用户说「都试试 / 对比」时用逗号连多个或 `all`。
- 跑完把保存路径回给用户。失败时先加 `--debug` 看返回，再决定是换 `--mode` 还是 `--model`。
- 不要把 API key 打印或写进任何文件。

## 参数速查

| 参数 | 说明 |
|------|------|
| `--provider, -p` | provider 名，逗号分隔多个，或 `all` |
| `--image, -i` | 输入图，可多次（图生图/编辑） |
| `--mode` | `chat` \| `images` \| `gemini`，覆盖配置 |
| `--model, -m` | 覆盖模型名 |
| `--out, -o` | 单图输出文件名 |
| `--outdir` | 输出目录，默认 `./output` |
| `--size` | 尺寸如 `1024x1024`（images 模式） |
| `--debug` | 打印原始返回结构 |
| `--list` | 列出已配置 provider |
