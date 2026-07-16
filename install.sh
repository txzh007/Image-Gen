#!/bin/bash
# genimg skill 一键安装脚本 (Linux/Mac)

set -e

echo "🚀 开始安装 genimg skill..."

# 检测安装目标
if [ -d "$HOME/.claude/skills" ]; then
    TARGET="$HOME/.claude/skills/genimg"
    PLATFORM="Claude Code"
elif [ -d "$HOME/.opencode/skills" ]; then
    TARGET="$HOME/.opencode/skills/genimg"
    PLATFORM="OpenCode"
else
    echo "❌ 未检测到 Claude Code 或 OpenCode，安装到当前目录"
    TARGET="./genimg"
    PLATFORM="standalone"
fi

# 下载或复制文件
if command -v git &> /dev/null && [ -n "$GENIMG_REPO" ]; then
    echo "📦 从仓库克隆..."
    git clone "$GENIMG_REPO" "$TARGET"
else
    echo "📦 从当前目录复制..."
    mkdir -p "$TARGET"
    cp genimg.py SKILL.md README.md providers.example.json .gitignore "$TARGET/"
fi

cd "$TARGET"

# 生成配置文件
if [ ! -f providers.json ]; then
    cp providers.example.json providers.json
    echo "📝 已创建 providers.json，请编辑填入你的中转站信息"
fi

echo ""
echo "✅ 安装完成！位置: $TARGET"
echo ""
echo "📋 下一步："
echo "  1. 编辑 $TARGET/providers.json 填入中转站地址"
echo "  2. 设置环境变量: export GENIMG_API_KEY='sk-xxx'"
echo "  3. 测试: python $TARGET/genimg.py 'test' --provider image2 --debug"
echo ""
if [ "$PLATFORM" = "standalone" ]; then
    echo "💡 作为 skill 使用：将此目录移到 ~/.claude/skills/ 或 ~/.opencode/skills/"
fi
