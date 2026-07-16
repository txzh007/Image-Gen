# genimg skill 一键安装脚本 (Windows PowerShell)

Write-Host "🚀 开始安装 genimg skill..." -ForegroundColor Green

# 检测安装目标
$claudePath = "$env:USERPROFILE\.claude\skills"
$opencodePath = "$env:USERPROFILE\.opencode\skills"

if (Test-Path $claudePath) {
    $target = "$claudePath\genimg"
    $platform = "Claude Code"
} elseif (Test-Path $opencodePath) {
    $target = "$opencodePath\genimg"
    $platform = "OpenCode"
} else {
    Write-Host "❌ 未检测到 Claude Code 或 OpenCode，安装到当前目录" -ForegroundColor Yellow
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
    Copy-Item genimg.py, SKILL.md, README.md, providers.example.json, .gitignore -Destination $target
}

Set-Location $target

# 生成配置文件
if (-not (Test-Path providers.json)) {
    Copy-Item providers.example.json providers.json
    Write-Host "📝 已创建 providers.json，请编辑填入你的中转站信息" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "✅ 安装完成！位置: $target" -ForegroundColor Green
Write-Host ""
Write-Host "📋 下一步："
Write-Host "  1. 编辑 $target\providers.json 填入中转站地址"
Write-Host "  2. 设置环境变量: `$env:BANANA_API_KEY='sk-xxx'"
Write-Host "  3. 测试: python $target\genimg.py 'test' --provider banana --debug"
Write-Host ""
if ($platform -eq "standalone") {
    Write-Host "💡 作为 skill 使用：将此目录移到 $env:USERPROFILE\.claude\skills\ 或 .opencode\skills\"
}
