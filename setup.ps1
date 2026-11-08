# 口播工作台 - 一键安装（Python 3.12 + FFmpeg + 语音识别模型）
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Need-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw '没有找到 winget。请在 Microsoft Store 里安装或更新「应用安装程序 (App Installer)」后再运行。'
    }
}

# 路径只用 PowerShell 自己拼，不读 Python 打印出来的路径：
# 用户名是中文时，Python 输出的路径会被解码成乱码。
function Find-Python {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe')
    )
    foreach ($p in $candidates) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

Write-Host '[1/4] 检查 Python 3.12 ...' -ForegroundColor Cyan
$python = Find-Python
if (-not $python) {
    Need-Winget
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    Refresh-Path
    $python = Find-Python
    if (-not $python) { throw 'Python 安装后仍找不到，请重启电脑后再运行 install.bat。' }
}
Write-Host "    使用 $python"

Write-Host '[2/4] 检查 FFmpeg ...' -ForegroundColor Cyan
$wingetLink = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ffmpeg.exe'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and -not (Test-Path $wingetLink)) {
    Need-Winget
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    Refresh-Path
}
Write-Host '    FFmpeg 就绪'

Write-Host '[3/4] 安装语音识别组件（faster-whisper）...' -ForegroundColor Cyan
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPy)) {
    & $python -m venv .venv
    if (-not (Test-Path -LiteralPath $venvPy)) { throw '创建 Python 运行环境失败。' }
}
& $venvPy -m pip install --upgrade pip -q
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host '    官方源失败，改用清华镜像重试 ...'
    & $venvPy -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) { throw '组件安装失败，请检查网络后重新运行 install.bat。' }
}

Write-Host '[4/4] 下载语音模型（约 1.5GB，只需一次）...' -ForegroundColor Cyan
& $venvPy -c "import asr; asr.load_model()"
if ($LASTEXITCODE -ne 0) { throw '模型下载失败，请检查网络后重新运行 install.bat。' }

Write-Host ''
Write-Host '安装完成！双击「start.bat」启动工作台。' -ForegroundColor Green
