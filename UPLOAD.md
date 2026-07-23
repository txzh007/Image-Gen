# 图床上传工具

## 概述

`upload.py` 是一个零配置的图片上传工具，支持多个免费图床服务。主要用于视频生成任务中需要将本地文件转换为公网 URL 的场景。

## 为什么需要图床？

- **图片生成和编辑**：不需要图床，脚本直接读取本地文件
- **视频生成**：必须使用公网 URL，因为视频 API 不接受本地文件路径

## 支持的图床服务

| 服务 | API Key | 文件大小限制 | 特点 | 推荐场景 |
|------|---------|-------------|------|---------|
| **Catbox** | 不需要 | 200MB | 默认推荐，永久保存 | 通用场景 |
| **Telegraph** | 不需要 | 5MB | Telegram 官方，稳定 | 小图片 |
| **SM.MS** | 可选 | 5MB | 国内访问快 | 国内用户 |
| **ImgBB** | 需要 | 未公开 | 需注册获取 key | 需要管理功能 |

## 使用方法

### 独立使用

```bash
# 使用默认 Catbox（推荐）
python3 upload.py ./image.png
# 输出: https://files.catbox.moe/xxxxx.png

# 使用 Telegraph
python3 upload.py ./image.png --service telegraph
# 输出: https://telegra.ph/file/xxxxx.png

# 使用 SM.MS
python3 upload.py ./image.png --service smms

# 使用 ImgBB（需要 API key）
python3 upload.py ./image.png --service imgbb --api-key YOUR_KEY
```

### 集成到视频生成

最简单的方式是使用 `--auto-upload` 参数：

```bash
# 自动上传本地图片
python3 genvideo.py "让这只猫飞起来" \
  --seconds 10 \
  --image ./local-cat.png \
  --auto-upload catbox \
  --out ./output/flying-cat.mp4
```

脚本会自动：
1. 检测到 `./local-cat.png` 是本地文件
2. 上传到 Catbox
3. 使用返回的 URL 创建视频任务
4. 在终端显示上传进度和结果

### 环境变量配置

某些服务支持通过环境变量提供 API key：

```bash
# ImgBB
export IMGBB_API_KEY="your-api-key"
python3 upload.py ./image.png --service imgbb

# SM.MS（可选）
export SMMS_API_KEY="your-api-key"
python3 upload.py ./image.png --service smms
```

## 获取 API Key

### ImgBB

1. 访问 https://imgbb.com/signup 注册账号
2. 访问 https://api.imgbb.com/ 获取 API key
3. 免费账号有速率限制，但无上传数量限制

### SM.MS

1. 访问 https://sm.ms/register 注册账号（可选）
2. 登录后访问 https://sm.ms/home/apitoken 获取 API Token
3. 匿名使用：每天 10 张，每张 5MB
4. 注册后：每分钟 20 次请求

## 故障排除

### 上传失败 HTTP 412/400

某些图床服务对请求格式敏感。解决方案：
1. 尝试其他图床服务
2. 检查文件大小是否超过限制
3. 确保文件格式是常见的图片格式（PNG、JPG、GIF、WebP）

### 文件太大

- Telegraph 限制 5MB：压缩图片或使用 Catbox
- SM.MS 限制 5MB：压缩图片或使用 Catbox
- Catbox 限制 200MB：几乎所有图片都能上传

### 网络问题

```bash
# 使用调试模式查看详细错误
python3 upload.py ./image.png --debug
```

## 最佳实践

1. **优先使用 Catbox**：无需配置，限制最宽松
2. **自动上传模式**：在 `genvideo.py` 中使用 `--auto-upload catbox`，无需手动上传
3. **保留原始文件**：上传不会删除本地文件
4. **记录 URL**：某些图床的 URL 是永久的，可以重复使用

## 安全提示

- 上传的文件会公开在互联网上，任何人都可以访问
- 不要上传包含敏感信息的图片（个人信息、密码、私密照片等）
- API key 是敏感信息，不要分享给他人或提交到 Git
- 使用环境变量或 macOS Keychain 存储 API key

## 常见问题

**Q: 图床服务会永久保存文件吗？**
A: 
- Catbox：永久保存（除非被举报）
- Telegraph：永久保存
- SM.MS：永久保存
- ImgBB：根据账号类型，免费账号可能有时间限制

**Q: 上传速度慢怎么办？**
A: 
- 国内用户可以使用 SM.MS
- 压缩图片文件大小
- 检查网络连接

**Q: 可以上传视频吗？**
A: Catbox 支持视频（最大 200MB），但其他服务主要针对图片。视频参考素材也可以先上传到 Catbox。

**Q: 需要删除已上传的文件怎么办？**
A: 大多数免费图床不提供删除功能。ImgBB 和 SM.MS 在注册后可以管理已上传的文件。

## 示例工作流

### 创建带参考图的视频

```bash
# 1. 准备本地素材
ls ./assets/
# cat.png  background.jpg

# 2. 直接使用，自动上传
python3 genvideo.py "结合猫和背景创作视频" \
  --seconds 10 \
  --image ./assets/cat.png \
  --image ./assets/background.jpg \
  --auto-upload catbox \
  --out ./output/result.mp4

# 脚本会输出：
# [upload] 检测到本地文件 ./assets/cat.png，正在上传到 catbox...
# [upload] ✓ 上传成功: https://files.catbox.moe/xxxxx.png
# [upload] 检测到本地文件 ./assets/background.jpg，正在上传到 catbox...
# [upload] ✓ 上传成功: https://files.catbox.moe/yyyyy.jpg
# [video] POST https://your-api/v1/videos (model=video-ds-2.0-fast, seconds=10)
# ...
```

### 手动控制上传

```bash
# 1. 先上传并保存 URL
CAT_URL=$(python3 upload.py ./cat.png)
echo "Cat URL: $CAT_URL"

# 2. 使用 URL 创建视频
python3 genvideo.py "让这只猫飞起来" \
  --seconds 10 \
  --image "$CAT_URL" \
  --out ./output/flying-cat.mp4
```

## 技术细节

- 零第三方依赖，仅使用 Python 标准库
- 使用 `urllib.request` 构造 multipart/form-data 请求
- 自动检测 MIME 类型
- 支持文件大小检查
- 错误信息清晰，便于排查问题

## 贡献

欢迎添加更多图床服务支持！常见的候选服务：
- Imgur（需要 OAuth，较复杂）
- Cloudflare R2（需要 S3 凭证）
- 自建图床（需要自定义接口）
