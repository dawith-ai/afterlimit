# AfterLimit 설치 (Windows) — Task Scheduler 로 5분마다 백그라운드 실행.
# launchd(macOS)/systemd(Linux) 에 대응하는 Windows 경로.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'AfterLimit'

function Get-AfterLimitCommand {
    # 콘솔 스크립트가 PATH 에 있으면 그걸, 없으면 python -m 으로 실행
    $exe = Get-Command afterlimit -ErrorAction SilentlyContinue
    if ($exe) { return @{ Program = $exe.Source; Args = 'run' } }
    return @{ Program = 'python'; Args = '-m afterlimit.cli run' }
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "제거했습니다: $TaskName"
    } else {
        Write-Host "등록된 작업이 없습니다."
    }
    return
}

# Python 3.11+ 확인
$pyOk = $false
try { $pyOk = (python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"; $LASTEXITCODE -eq 0) } catch {}
if (-not $pyOk) { throw "Python 3.11 이상이 필요합니다." }

# 패키지 설치 (아직 없으면)
if (-not (Get-Command afterlimit -ErrorAction SilentlyContinue)) {
    Write-Host "afterlimit 설치 중..."
    python -m pip install --user --upgrade . | Out-Null
}

$cmd = Get-AfterLimitCommand
$action  = New-ScheduledTaskAction -Execute $cmd.Program -Argument $cmd.Args
# 5분 간격으로 무기한 반복 (launchd StartInterval 300 / systemd OnUnitActiveSec=5min 대응)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
# 로그인 세션에서, 창 없이. claude 인증(사용자 홈)을 읽어야 하므로 사용자 계정으로.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "등록했습니다: '$TaskName' (5분 간격)."
Write-Host ""
Write-Host "확인:"
Write-Host "  afterlimit scan     # 한도로 멈춘 세션"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
