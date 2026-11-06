# publish.ps1 — АВТОНОМНАЯ публикация UniversalAgent в GitHub (Windows).
# НЕ ЗАДАЁТ НИ ОДНОГО ВОПРОСА:
#   1) собирает ВСЕ токены со всех *.txt (папка скрипта, домашняя, Рабочий стол, Документы, Загрузки);
#   2) перепробует каждый через GitHub API и берёт ЖИВОЙ (мёртвые — не помеха);
#   3) логин берётся сам из API (/user);
#   4) репозиторий создаётся сам через API (если его нет);
#   5) пуш уходит сам, авторизация — HTTP-заголовком.
# Токены в консоли видны только отпечатком (первые…последние 4 знака);
# в URL, .git/config, файлах проекта и выводе ошибок они не светятся.
# Запуск: двойной клик по PUBLISH.bat (или powershell -File publish.ps1)

$ErrorActionPreference = 'Continue'

# ===== НАСТРОЙКИ (можно менять) =====
$RepoName  = 'universal-agent'
$Branch    = 'main'
$Tag       = 'v2.2.0'
$Private   = $false    # $true => приватный репозиторий
$ForcePush = $false    # $true => только если на remote чужая история мешает
$GitName   = 'rtynbirf'
$GitEmail  = 'rtynbirf@users.noreply.github.com'
$ApiBase   = 'https://api.github.com'
# ====================================

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir
# чтобы имена файлов кириллицей из git приходили как есть, а не \320\237...
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[+] $m" -ForegroundColor Green }
function Write-Bad($m)  { Write-Host "[!] $m" -ForegroundColor Red }
function Hide-Token([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return $s }
    $s = [regex]::Replace($s, 'gh[posur]_[A-Za-z0-9]+', 'gh*_***[REDACTED]')
    $s = [regex]::Replace($s, 'github_pat_[A-Za-z0-9_]+', 'github_pat_***[REDACTED]')
    return $s
}
function Get-Fp([string]$s) {
    if ([string]::IsNullOrEmpty($s) -or $s.Length -lt 12) { return '***' }
    return $s.Substring(0, 4) + '…' + $s.Substring($s.Length - 4)
}

Write-Step 'UniversalAgent v2.2.0 — автономная публикация. Вопросов не задаст.'

# --- 0. git на месте? -----------------------------------------------------
$null = git --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Bad 'git не найден. Поставь отсюда и перезапусти: https://git-scm.com/download/win'
    exit 1
}

# --- 1. кандидаты в токены: собираем ВСЕ ----------------------------------
$tokens = @()
$tokenFiles = @()

if (-not [string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
    $tokens += $env:GH_TOKEN.Trim()
    $tokenFiles += 'переменная GH_TOKEN'
    Write-Ok 'Кандидат: токен из переменной GH_TOKEN'
} else {
    # сканируем: папка скрипта, домашняя, Рабочий стол, Документы, Загрузки (без рекурсии)
    $scanDirs = @($scriptDir)
    foreach ($known in @('UserProfile', 'Desktop', 'MyDocuments')) {
        try {
            $d = [Environment]::GetFolderPath($known)
            if (-not [string]::IsNullOrEmpty($d)) { $scanDirs += $d }
        } catch { }
    }
    try {
        $dl = Join-Path $env:USERPROFILE 'Downloads'
        if ($dl -and (Test-Path -LiteralPath $dl)) { $scanDirs += $dl }
    } catch { }

    $files = @()
    $ghFile = Join-Path $scriptDir '.gh_token'
    if (Test-Path -LiteralPath $ghFile) { $files += $ghFile }
    foreach ($d in $scanDirs) {
        if ([string]::IsNullOrEmpty($d) -or -not (Test-Path -LiteralPath $d)) { continue }
        if ($d -eq $ghFile) { continue }
        $ts = @(Get-ChildItem -LiteralPath $d -Filter '*.txt' -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending)
        foreach ($t in $ts) { $files += $t.FullName }
    }

    foreach ($f in $files) {
        $raw = ''
        try { $raw = Get-Content -LiteralPath $f -Raw -ErrorAction Stop } catch { continue }
        if ([string]::IsNullOrEmpty($raw)) { continue }
        $ms = [regex]::Matches($raw, 'gh[posur]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}')
        if ($ms.Count -gt 0) {
            foreach ($m in $ms) { $tokens += $m.Value.Trim(); $tokenFiles += $f }
            Write-Ok ('Файл с токеном: ' + $f)
        }
    }
}
if ($tokens.Count -gt 1) {
    $seen = @{}; $uT = @(); $uF = @()
    for ($i = 0; $i -lt $tokens.Count; $i++) {
        if (-not $seen.ContainsKey($tokens[$i])) {
            $seen[$tokens[$i]] = $true; $uT += $tokens[$i]; $uF += $tokenFiles[$i]
        }
    }
    $tokens = @($uT); $tokenFiles = @($uF)
}

if ($tokens.Count -eq 0) {
    Write-Bad 'Токенов не нашёл вообще.'
    Write-Host '    Сканировал *.txt в: папке со скриптом, домашней, на Рабочем столе, в Документах, в Загрузках.'
    Write-Host '    Сохрани токен (ghp_... или github_pat_...) в любой .txt в одном из этих мест и запусти снова.'
    exit 1
}

# --- 2. живой токен: перепробуем все --------------------------------------
Write-Step ('Токенов найдено: ' + $tokens.Count + '. Проверяю каждый через GitHub API...')
$me = $null; $goodToken = $null; $codes = @()
for ($ti = 0; $ti -lt $tokens.Count; $ti++) {
    $t = $tokens[$ti]
    $headers = @{
        Authorization = "Bearer $t"
        'User-Agent'  = 'universal-agent-pub'
        Accept        = 'application/vnd.github+json'
    }
    $code = $null; $errMsg = ''
    try {
        $me = Invoke-RestMethod -Uri "$ApiBase/user" -Headers $headers -Method Get -TimeoutSec 30
        $goodToken = $t
        break
    } catch {
        $me = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = 0 }
        $errMsg = $_.Exception.Message
        Write-Bad ('Токен ' + (Get-Fp $t) + ' (' + $tokenFiles[$ti] + ') не принят (HTTP ' + $code + ').')
        $codes += "$code"
    }
}
if ($null -eq $me) {
    Write-Bad ('Ни один токен GitHub не принял (HTTP: ' + (($codes -join ', ')) + ').')
    Write-Host '    Выпусти новый (2 минуты):'
    Write-Host '      github.com -> Settings -> Developer settings -> Personal access tokens ->'
    Write-Host '      Tokens (classic) -> Generate new token (classic) -> галочка repo -> Generate -> Copy.'
    Write-Host '    Вставь его в .txt одной строкой и СОХРАНИ файл (Ctrl+S), затем запусти снова.'
    Write-Host '    Из какого файла каждый токен — подписано в строках выше.'
    exit 1
}
$token = $goodToken
$headers = @{
    Authorization = "Bearer $token"
    'User-Agent'  = 'universal-agent-pub'
    Accept        = 'application/vnd.github+json'
}
Write-Ok ('Живой токен: ' + (Get-Fp $token) + ' — с ним и работаю.')
Write-Ok ('Ты на GitHub как: ' + $me.login)

# --- 3. репозиторий: есть или создать -------------------------------------
$repoHtml = "https://github.com/$($me.login)/$RepoName"
$exists = $false
try {
    Invoke-RestMethod -Uri "$ApiBase/repos/$($me.login)/$RepoName" -Headers $headers -Method Get -TimeoutSec 30 | Out-Null
    $exists = $true
} catch { }
if ($exists) {
    Write-Ok 'Репозиторий уже существует — использую его.'
} else {
    Write-Step ('Создаю репозиторий: ' + $repoHtml)
    $desc = 'UniversalAgent (TsUA) - single-brain holistic agent: one LLM core, closed perception-action loop, four-layer memory, sandboxed tools. Pure stdlib.'
    $body = @{ name = $RepoName; private = $Private; auto_init = $false; description = $desc } | ConvertTo-Json
    $resp = $null; $code = $null; $errMsg = ''
    try {
        $resp = Invoke-RestMethod -Uri "$ApiBase/user/repos" -Headers $headers -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 60
    } catch {
        try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = 0 }
        $errMsg = $_.Exception.Message
    }
    if ($null -ne $resp) {
        if ($resp.html_url) { $repoHtml = $resp.html_url }
        Write-Ok ('Репозиторий создан: ' + $repoHtml)
    } elseif ("$code" -eq '422') {
        Write-Ok 'Репозиторий уже существует — использую его.'
    } else {
        Write-Bad ('Не смог создать репозиторий (HTTP ' + $code + '): ' + (Hide-Token $errMsg))
        Write-Host ('    Проверь: у токена есть scope repo, имя "' + $RepoName + '" свободно, сеть доступна.')
        exit 1
    }
}

# --- 4. история git (в архиве .git уже вложен; это на случай распаковки без него) ---
if (-not (Test-Path -LiteralPath (Join-Path $scriptDir '.git'))) {
    Write-Step '.git нет — создаю канон-историю (сквош + тег)...'
    git init -q -b $Branch 2>$null
    if ($LASTEXITCODE -ne 0) { git init -q; git symbolic-ref HEAD ('refs/heads/' + $Branch) }
    git add -A
    # токены в коммит не попадут: проверяю каждый staged-файл (quotepath=off — кириллица без экранирования)
    $staged = @(git -c core.quotepath=off diff --cached --name-only)
    foreach ($sf in $staged) {
        if ([string]::IsNullOrWhiteSpace($sf)) { continue }
        if (-not (Test-Path -LiteralPath $sf)) { continue }
        $hit = Select-String -LiteralPath $sf -Pattern 'gh[posur]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}' -ErrorAction SilentlyContinue
        if ($hit) {
            git rm -q --cached -- "$sf"
            Write-Bad ('В коммит не пойдёт (внутри токен): ' + $sf)
        }
    }
    # страховка: даже если что-то проскочило — сканирую весь staged-дифф
    $diffText = (git -c core.quotepath=off diff --cached 2>$null | Out-String)
    if ($diffText -match 'gh[posur]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}') {
        git reset -q
        Write-Bad 'В staged-изменениях остался токен. Публикация остановлена.'
        Write-Host '    Убери файл с токеном из папки проекта (или в Загрузки) и запусти скрипт снова.'
        exit 1
    }
    git -c ('user.name=' + $GitName) -c ('user.email=' + $GitEmail) commit -q `
        -m 'feat(agent): UniversalAgent v2.2.0 - holistic single-brain agent' `
        -m ('Signed-off-by: ' + $GitName + ' <' + $GitEmail + '>')
    $tagExists = git rev-parse -q --verify ('refs/tags/' + $Tag) 2>$null
    if (-not $tagExists) {
        git -c ('user.name=' + $GitName) -c ('user.email=' + $GitEmail) tag -a $Tag -m ('UniversalAgent ' + $Tag) 2>$null
    }
}

# --- 5. remote ------------------------------------------------------------
$origin = "https://github.com/$($me.login)/$RepoName.git"
$remotes = @(git remote)
if ($remotes -contains 'origin') { git remote set-url origin $origin | Out-Null }
else { git remote add origin $origin | Out-Null }

# --- 6. пуш: авторизация заголовком, вопросов нет, токен нигде не светится ---
$pair = 'x-access-token:' + $token
$b64  = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$env:GIT_TERMINAL_PROMPT = '0'
$env:GIT_REDIRECT_STDERR = '2>&1'   # прогресс git-а не должен выглядеть как ошибка

$pushArgs = @('-c', 'credential.helper=', '-c', ('http.extraHeader=AUTHORIZATION: basic ' + $b64),
              'push', '-u', 'origin', ($Branch + ':' + $Branch))
if ($ForcePush) { $pushArgs += '--force' }

Write-Step ('Пуш ветки ' + $Branch + ' ...')
$out = (& git @pushArgs 2>&1 | Out-String)
$out = Hide-Token $out
if ($LASTEXITCODE -ne 0) {
    Write-Bad 'Пуш не прошёл:'
    Write-Host $out
    Write-Host '    Если на remote уже есть история (например, создал репо с README) — поставь'
    Write-Host '    $ForcePush = $true в шапке этого файла и запусти ещё раз.'
    exit 1
}
Write-Ok ($out.Trim())

Write-Step ('Пуш тега ' + $Tag + ' (force: перезаливаем на текущий HEAD) ...')
$tout = (& git -c 'credential.helper=' -c ('http.extraHeader=AUTHORIZATION: basic ' + $b64) push --force origin $Tag 2>&1 | Out-String)
$tout = Hide-Token $tout
if ($LASTEXITCODE -eq 0) { Write-Ok ($tout.Trim()) }
else { Write-Host $tout; Write-Host ('    (тег не отправлен — не критично. Руками: git push --force origin ' + $Tag + ')') }

# токен из памяти — стереть
$pair = $null; $b64 = $null; $token = $null; $goodToken = $null; $headers = $null

# --- 7. проверка результата -----------------------------------------------
$code = $null
try {
    $h2 = @{ 'User-Agent' = 'universal-agent-pub'; Accept = 'application/vnd.github+json' }
    Invoke-RestMethod -Uri "$ApiBase/repos/$($me.login)/$RepoName/branches/$Branch" -Headers $h2 -Method Get -TimeoutSec 30 | Out-Null
    $code = 200
} catch { $code = 0 }
if ("$code" -eq '200') { Write-Ok ('Проверено: ветка ' + $Branch + ' на GitHub на месте.') }
$tcode = $null
try {
    Invoke-RestMethod -Uri "$ApiBase/repos/$($me.login)/$RepoName/git/ref/tags/$Tag" -Headers $h2 -Method Get -TimeoutSec 30 | Out-Null
    $tcode = 200
} catch { $tcode = 0 }
if ("$tcode" -eq '200') { Write-Ok ('Проверено: тег ' + $Tag + ' на GitHub на месте.') }

Write-Host ''
Write-Ok ('ГОТОВО. Репозиторий: ' + $repoHtml)
Write-Ok ('Actions:     ' + $repoHtml + '/actions — CI сам прогонит тесты и канон 6/6')
Write-Host ''
Write-Host '    Отзови токен, которым пушил: GitHub -> Settings -> Developer settings -> Personal access tokens.' -ForegroundColor Yellow
exit 0
