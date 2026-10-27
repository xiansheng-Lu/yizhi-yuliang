$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path $PSScriptRoot -Parent
$infrastructureFile = Join-Path $backendRoot 'deployment/.env.infrastructure'
$applicationFile = Join-Path $backendRoot '.env'
if ((Test-Path -LiteralPath $infrastructureFile) -or (Test-Path -LiteralPath $applicationFile)) {
    throw '已有本地配置，请复用并手动核对；脚本不会覆盖现有配置。'
}
function New-LocalSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}
$databasePassword = New-LocalSecret
$appPassword = New-LocalSecret
$authPassword = New-LocalSecret
$migrationPassword = New-LocalSecret
$redisPassword = New-LocalSecret
$brokerPassword = New-LocalSecret
$objectPassword = New-LocalSecret
$sessionKey = New-LocalSecret
$infrastructureLines = @(
    "YULIANG_POSTGRES_PASSWORD=$databasePassword",
    "YULIANG_APP_PASSWORD=$appPassword",
    "YULIANG_AUTH_PASSWORD=$authPassword",
    "YULIANG_MIGRATION_PASSWORD=$migrationPassword",
    "YULIANG_REDIS_PASSWORD=$redisPassword",
    "YULIANG_BROKER_PASSWORD=$brokerPassword",
    "YULIANG_MINIO_PASSWORD=$objectPassword"
)
$applicationLines = @(
    'YULIANG_ENVIRONMENT=local',
    "YULIANG_DATABASE_URL=postgresql+psycopg://app_login:${appPassword}@127.0.0.1:15432/yizhi",
    "YULIANG_AUTH_DATABASE_URL=postgresql+psycopg://app_auth:${authPassword}@127.0.0.1:15432/yizhi",
    "YULIANG_REDIS_URL=redis://:${redisPassword}@127.0.0.1:16379/0",
    "YULIANG_BROKER_URL=amqp://yuliang:${brokerPassword}@127.0.0.1:15672/yuliang",
    "YULIANG_SESSION_SECRET=$sessionKey",
    'YULIANG_ALLOWED_ORIGINS=["http://localhost:3000"]'
)
$utf8 = New-Object Text.UTF8Encoding $false
[IO.File]::WriteAllLines($infrastructureFile, $infrastructureLines, $utf8)
[IO.File]::WriteAllLines($applicationFile, $applicationLines, $utf8)
Write-Output '本地占位环境已生成，随机密钥仅保存到Git忽略文件，未启动服务。'
