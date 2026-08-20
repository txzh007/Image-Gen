#!/bin/bash
# genimg skill 一键安装脚本 (Linux/Mac)

set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

echo "🚀 开始安装 genimg skill..."

# 检测安装目标；GENIMG_TARGET 可显式覆盖。
if [ -n "${GENIMG_TARGET:-}" ]; then
    TARGET="$GENIMG_TARGET"
    PLATFORM="custom"
elif [ -d "$HOME/.codex/skills" ]; then
    TARGET="$HOME/.codex/skills/genimg"
    PLATFORM="Codex"
elif [ -d "$HOME/.claude/skills" ]; then
    TARGET="$HOME/.claude/skills/genimg"
    PLATFORM="Claude Code"
elif [ -d "$HOME/.opencode/skills" ]; then
    TARGET="$HOME/.opencode/skills/genimg"
    PLATFORM="OpenCode"
else
    echo "⚠ 未检测到 Codex、Claude Code 或 OpenCode，安装到当前目录"
    TARGET="./genimg"
    PLATFORM="standalone"
fi

# 下载或复制文件
if command -v git &> /dev/null && [ -n "${GENIMG_REPO:-}" ]; then
    echo "📦 从仓库克隆..."
    git clone "$GENIMG_REPO" "$TARGET"
else
    echo "📦 从当前目录复制..."
    mkdir -p "$TARGET"
    cp \
        "$SCRIPT_DIR/genimg.py" \
        "$SCRIPT_DIR/genvideo.py" \
        "$SCRIPT_DIR/upload.py" \
        "$SCRIPT_DIR/configure-macos.sh" \
        "$SCRIPT_DIR/SKILL.md" \
        "$SCRIPT_DIR/README.md" \
        "$SCRIPT_DIR/providers.example.json" \
        "$SCRIPT_DIR/LICENSE" \
        "$TARGET/"
    mkdir -p "$TARGET/agents"
    cp "$SCRIPT_DIR/agents/openai.yaml" "$TARGET/agents/openai.yaml"
fi

cd "$TARGET"

echo ""
echo "✅ 安装完成！位置: $TARGET"
echo ""
echo "📋 下一步："
echo "  1. 设置环境变量: export IMAGE_API_BASE='https://你的中转站/v1'; export GENIMG_API_KEY='sk-xxx'"
echo "  2. 测试: python3 genimg.py 'test' --provider banana --dry-run"
echo "  3. 视频 dry-run: python3 genvideo.py 'test video' --dry-run"
echo ""
if [ "$PLATFORM" = "standalone" ]; then
    echo "💡 作为 skill 使用：将此目录移到 ~/.codex/skills/、~/.claude/skills/ 或 ~/.opencode/skills/"
fi
