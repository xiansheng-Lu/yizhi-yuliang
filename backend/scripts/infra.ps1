param([ValidateSet('Check', 'Start', 'Stop', 'Restart', 'Reinitialize')][string]$Action = 'Check')
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path $PSScriptRoot -Parent
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'BLOCKED M0-04：缺少Docker CLI和可用Linux容器引擎；需要就绪后真实验证。'
    exit 2
}
& docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw 'BLOCKED M0-04：Docker引擎不可用。' }
$localEnvFile = Join-Path $backendRoot 'deployment/.env.infrastructure'
if (-not (Test-Path -LiteralPath $localEnvFile)) {
    throw '请先运行prepare-local.ps1生成独立本地配置，不允许使用默认共享密码。'
}
$composeArgs = @('compose', '--project-name', 'yuliang-local', '--env-file', $localEnvFile,
    '--file', (Join-Path $backendRoot 'docker-compose.yml'))
switch ($Action) {
    'Check' { & docker @composeArgs config --quiet }
    'Start' { & docker @composeArgs up -d --wait }
    'Stop' { & docker @composeArgs stop }
    'Restart' { & docker @composeArgs restart }
    'Reinitialize' {
        # 仅重放幂等角色/扩展初始化，不删除卷或业务数据。
        & docker @composeArgs exec -T postgres sh /docker-entrypoint-initdb.d/10-yuliang.sh
    }
}
if ($LASTEXITCODE -ne 0) { throw "基础设施操作失败：$Action；不得记验收通过。" }
