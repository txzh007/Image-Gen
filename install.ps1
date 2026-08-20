# genimg skill 一键安装脚本 (Windows PowerShell)

Write-Host "🚀 开始安装 genimg skill..." -ForegroundColor Green

$scriptDir = $PSScriptRoot

# 检测安装目标
$claudePath = "$env:USERPROFILE\.claude\skills"
$opencodePath = "$env:USERPROFILE\.opencode\skills"
$codexPath = "$env:USERPROFILE\.codex\skills"

if ($env:GENIMG_TARGET) {
    $target = $env:GENIMG_TARGET
    $platform = "custom"
} elseif (Test-Path $codexPath) {
    $target = "$codexPath\genimg"
    $platform = "Codex"
} elseif (Test-Path $claudePath) {
    $target = "$claudePath\genimg"
    $platform = "Claude Code"
} elseif (Test-Path $opencodePath) {
    $target = "$opencodePath\genimg"
    $platform = "OpenCode"
} else {
    Write-Host "⚠ 未检测到 Codex、Claude Code 或 OpenCode，安装到当前目录" -ForegroundColor Yellow
    $target = ".\genimg"
    $platform = "standalone"
}

# 下载或复制文件
if ((Get-Command git -ErrorAction SilentlyContinue) -and $env:GENIMG_REPO) {
    Write-Host "📦 从仓库克隆..."
    git clone $env:GENIMG_REPO $target
} else {
    Write-Host "📦 从当前目录复制..."
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item `
        "$scriptDir\genimg.py", `
        "$scriptDir\genvideo.py", `
        "$scriptDir\upload.py", `
        "$scriptDir\SKILL.md", `
        "$scriptDir\README.md", `
        "$scriptDir\providers.example.json", `
        "$scriptDir\LICENSE" `
        -Destination $target
    New-Item -ItemType Directory -Path "$target\agents" -Force | Out-Null
    Copy-Item "$scriptDir\agents\openai.yaml" -Destination "$target\agents\openai.yaml"
}

Set-Location $target

Write-Host ""
Write-Host "✅ 安装完成！位置: $target" -ForegroundColor Green
Write-Host ""
Write-Host "📋 下一步："
Write-Host "  1. 设置环境变量: `$env:IMAGE_API_BASE='https://你的中转站/v1'; `$env:GENIMG_API_KEY='sk-xxx'"
Write-Host "  2. 测试: python genimg.py 'test' --provider banana --dry-run"
Write-Host "  3. 视频 dry-run: python genvideo.py 'test video' --dry-run"
Write-Host ""
if ($platform -eq "standalone") {
    Write-Host "💡 作为 skill 使用：将此目录移到 .codex\skills\、.claude\skills\ 或 .opencode\skills\"
}
