param(
    [Parameter(Position = 0)]
    [string]$Task = "serve",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TaskArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $repoRoot

# 使用仓库内缓存，避免默认用户目录没有写权限。
$env:PIXI_CACHE_DIR = Join-Path $repoRoot ".pixi-cache"
$env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache"

& pixi install
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& pixi run $Task @TaskArgs
exit $LASTEXITCODE
