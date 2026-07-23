# 图片生成与编辑测试报告

测试时间：2026-07-24 02:52
测试环境：IMAGE_API_BASE = https://token.gufacoding.com/v1

## 测试结果汇总

### ✅ 成功的测试

#### 1. Banana 文生图
**命令：**
```bash
python3 genimg.py "一只可爱的橙色小猫，坐在窗台上看着外面的雨，温暖的室内灯光，电影感摄影，柔和色调" \
  --provider banana \
  --aspect-ratio 16:9 \
  --quality 2K
```

**结果：**
- ✅ 成功生成：`output/banana_20260724-024624_1.png` (7273 KB)
- 模型：gemini-3-pro-image
- 接口：/images/generations
- 输出格式：base64

**备注：** 有一个不影响功能的警告 "保存失败: unknown url type: ''"

---

#### 2. Banana 图片编辑
**命令：**
```bash
python3 genimg.py "只把背景改成晴朗的海边日落，金色阳光；必须保持小猫的外观、颜色、姿势和位置完全不变" \
  --provider banana \
  --image ./output/banana_20260724-024624_1.png
```

**结果：**
- ✅ 成功编辑：`output/cat-sunset-beach.png` (1559 KB)
- 模式：chat (多模态编辑)
- 接口：/chat/completions
- 输出格式：base64

**备注：** 提示 "mode=chat 没有通用映射，已忽略: aspect_ratio, quality" - 这是预期行为

---

#### 3. Image2 文生图
**命令：**
```bash
python3 genimg.py "一只穿着宇航服的柴犬，站在月球表面，地球在背景中，科幻风格，写实渲染" \
  --provider image2 \
  --size 1536x1024 \
  --quality high \
  --output-format png
```

**结果：**
- ✅ 成功生成：`output/astronaut-dog.png` (2528 KB)
- 模型：gpt-image-2
- 接口：/images/generations
- 输出格式：base64

---

#### 4. 双 Provider 对比生成
**命令：**
```bash
python3 genimg.py "未来科技城市夜景，霓虹灯反射在湿润的街道上，赛博朋克风格，电影感构图" \
  --provider banana,image2 \
  --outdir ./output/compare-cyberpunk
```

**结果：**
- ✅ Banana 成功：`banana_20260724-025208_1.png` (2160 KB)
- ✅ Image2 成功：`image2_20260724-025308.png` (1795 KB)
- 两个模型都正常工作，可以并行生成对比

---

### ❌ 失败的测试

#### 5. Image2 图片编辑
**命令：**
```bash
python3 genimg.py "把月球表面改成火星地表，红色岩石和沙丘；保持柴犬的宇航服、姿势、面部表情和构图完全不变" \
  --provider image2 \
  --image ./output/astronaut-dog.png \
  --quality high
```

**结果：**
- ❌ 失败：HTTP 524 (Cloudflare Timeout)
- 接口：/images/edits
- 错误原因：中转站或上游服务超时

**分析：**
- HTTP 524 是 Cloudflare 的超时错误
- 可能是 `/images/edits` 接口处理时间过长
- 或者中转站的该接口配置有问题

---

## 功能验证总结

### ✅ 已验证功能

1. **Banana 文生图** - 完美工作
   - 支持 aspect-ratio 和 quality 参数
   - 输出高质量图片（2K）
   - base64 编码正常解析

2. **Banana 图片编辑** - 完美工作
   - 通过 Chat 多模态接口实现
   - 能够理解复杂的编辑指令
   - 保持原图特征的同时修改背景

3. **Image2 文生图** - 完美工作
   - 支持像素级尺寸控制
   - 支持 quality 和 output-format 参数
   - 生成质量良好

4. **多 Provider 并行生成** - 完美工作
   - 可以同时使用 banana 和 image2
   - 输出文件自动按 provider 命名
   - 方便对比不同模型效果

### ⚠️ 部分问题

1. **Banana 保存警告**
   - 出现 "保存失败: unknown url type: ''" 警告
   - 但实际从 base64 保存成功
   - 可能是响应中有空 URL 字段

2. **Image2 编辑超时**
   - HTTP 524 错误
   - 可能是中转站的 /images/edits 接口不稳定
   - 建议使用 Banana 的 Chat 编辑作为主要方式

---

## 生成的图片清单

```
output/
├── banana_20260724-024624_1.png          # 橙色小猫雨中窗台 (7.3 MB)
├── cat-sunset-beach.png                  # 小猫海边日落（编辑）(1.6 MB)
├── astronaut-dog.png                     # 宇航员柴犬月球 (2.5 MB)
└── compare-cyberpunk/
    ├── banana_20260724-025208_1.png      # 赛博朋克城市 Banana (2.2 MB)
    └── image2_20260724-025308.png        # 赛博朋克城市 Image2 (1.8 MB)
```

**总计：** 5 张图片成功生成，1 次编辑失败（超时）

---

## 推荐使用方式

### 文生图

**高质量、复杂指令：** 使用 Banana
```bash
python3 genimg.py "复杂的场景描述" \
  --provider banana \
  --aspect-ratio 16:9 \
  --quality 2K
```

**像素精确控制：** 使用 Image2
```bash
python3 genimg.py "场景描述" \
  --provider image2 \
  --size 1536x1024 \
  --quality high
```

**对比效果：** 同时使用两个 Provider
```bash
python3 genimg.py "场景描述" \
  --provider banana,image2 \
  --outdir ./output/compare
```

### 图片编辑

**推荐：** 使用 Banana Chat 编辑
```bash
python3 genimg.py "只修改背景...; 保持主体不变..." \
  --provider banana \
  --image ./input.png
```

**备选：** Image2 编辑（可能超时）
```bash
python3 genimg.py "编辑指令" \
  --provider image2 \
  --image ./input.png \
  --quality high
```

---

## 性能数据

| Provider | 操作 | 耗时估计 | 成功率 |
|---------|------|---------|--------|
| Banana | 文生图 | ~60s | 100% |
| Banana | 编辑 | ~60s | 100% |
| Image2 | 文生图 | ~60s | 100% |
| Image2 | 编辑 | 超时 | 0% (本次测试) |

---

## 问题和建议

### 当前问题

1. **Banana URL 警告**
   - 不影响功能，但应该排查代码中的 URL 解析逻辑
   - 可能是响应格式中有额外的空 URL 字段

2. **Image2 编辑不稳定**
   - HTTP 524 超时
   - 建议增加 `--timeout` 参数支持更长等待时间
   - 或者优先推荐使用 Banana Chat 编辑

### 改进建议

1. **超时处理**
   - 为 Image2 编辑增加更长的默认超时
   - 提供友好的重试机制

2. **错误提示**
   - HTTP 524 时提示用户可以：
     - 增加 `--timeout` 参数
     - 使用 Banana 编辑作为替代
     - 稍后重试

3. **文档更新**
   - 在 README 中说明 Image2 编辑可能遇到超时
   - 推荐 Banana 作为主要编辑方式

---

## 结论

✅ **核心功能完全可用**
- Banana 文生图和编辑：完美
- Image2 文生图：完美
- 多 Provider 并行：完美

⚠️ **部分功能有限制**
- Image2 编辑：不稳定（超时）

💡 **推荐配置**
- 文生图：Banana（复杂场景）或 Image2（精确尺寸）
- 图片编辑：优先使用 Banana Chat 模式
- 对比测试：使用 `--provider banana,image2`

整体来说，Image-Gen skill 功能完善，可以满足 AI agents 的图片生成和编辑需求！
