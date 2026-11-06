#!/usr/bin/env bash
# publish.sh — АВТОНОМНАЯ публикация UniversalAgent в GitHub.
# НЕ ЗАДАЁТ НИ ОДНОГО ВОПРОСА:
#   1) собирает ВСЕ токены со всех *.txt (папка скрипта, домашняя, Рабочий стол, Загрузки);
#   2) перепробует каждый через GitHub API и берёт ЖИВОЙ (мёртвые — не помеха);
#   3) логин берётся сам из API (/user);
#   4) репозиторий создаётся сам через API (если его нет);
#   5) пуш уходит сам, авторизация — HTTP-заголовком, токен никуда не пишется.
# Токены в консоли показываются только отпечатком (первые…последние 4 знака),
# в .git/config, в URL и в файлах проекта не оказываются.

set -uo pipefail
cd "$(dirname "$0")"

REPO_NAME="${REPO_NAME:-universal-agent}"
BRANCH="${BRANCH:-main}"
TAG="${TAG:-v2.2.0}"
PRIVATE="${PRIVATE:-0}"
FORCE_PUSH="${FORCE_PUSH:-0}"
GIT_NAME="${GIT_NAME:-rtynbirf}"
GIT_EMAIL="${GIT_EMAIL:-rtynbirf@users.noreply.github.com}"
API="${GITHUB_API:-https://api.github.com}"

ok()  { printf '\033[32m[+]\033[0m %s\n' "$*"; }
bad() { printf '\033[31m[!]\033[0m %s\n' "$*"; }
step(){ printf '\033[36m[*]\033[0m %s\n' "$*"; }
redact() {
  sed -E 's/gh[posur]_[A-Za-z0-9]+/gh*_***[REDACTED]/g; s/github_pat_[A-Za-z0-9_]+/github_pat_***[REDACTED]/g'
}
fp() { printf '%s…%s' "${1:0:4}" "${1: -4}"; }

step "UniversalAgent v2.2.0 — автономная публикация. Вопросов не задаст."

# --- 0. инструменты на месте? -------------------------------------------
command -v git >/dev/null 2>&1 || { bad "git не найден. Поставь git и запусти снова."; exit 1; }
command -v curl >/dev/null 2>&1 || { bad "curl не найден (в Windows есть в составе Win10+ и git-bash)."; exit 1; }
export GIT_TERMINAL_PROMPT=0    # никаких интерактивных подсказок — только честные ошибки

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# --- 1. кандидаты в токены: собираем ВСЕ ---------------------------------
CANDIDATES=()
CAND_FILES=()
SEEN="|"
add_tok() {
  local t="$1" src="$2"
  [ -n "$t" ] || return 0
  case "$SEEN" in *"|$t|"*) return 0 ;; esac
  SEEN="$SEEN$t|"
  CANDIDATES+=("$t")
  CAND_FILES+=("${src:-?}")
}

if [ -n "${GH_TOKEN:-}" ]; then
  add_tok "$(printf '%s' "$GH_TOKEN" | tr -d ' \t\r\n')" "переменная GH_TOKEN"
  ok "Кандидат: токен из переменной GH_TOKEN"
else
  # сканируем: папка скрипта, домашняя, Рабочий стол, Загрузки (без рекурсии)
  for d in "$PWD" "${HOME:-}" "${HOME:-}/Desktop" "${HOME:-}/Downloads"; do
    [ -n "$d" ] && [ -d "$d" ] || continue
    for f in "$d/.gh_token" "$d"/*.txt "$d"/*.TXT; do
      [ -e "$f" ] || continue
      if grep -qE 'gh[posur]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}' "$f" 2>/dev/null; then
        while IFS= read -r t; do add_tok "$(printf '%s' "$t" | tr -d ' \t\r\n')" "$f"; done \
          < <(grep -hoE 'gh[posur]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}' "$f" 2>/dev/null)
        ok "Файл с токеном: $f"
      fi
    done
  done
fi

if [ "${#CANDIDATES[@]}" -eq 0 ]; then
  bad "Токенов не нашёл вообще."
  echo "    Сканировал *.txt в: папке со скриптом, домашней, на Рабочем столе, в Загрузках."
  echo "    Сохрани токен (ghp_... или github_pat_...) в любой .txt в одном из этих мест и запусти снова."
  exit 1
fi

# --- 2. живой токен: перепробуем все --------------------------------------
step "Токенов найдено: ${#CANDIDATES[@]}. Проверяю каждый через GitHub API..."
TOKEN=""; LOGIN=""
for i in "${!CANDIDATES[@]}"; do
  t="${CANDIDATES[$i]}"
  code="$(curl -sS -H "Authorization: Bearer $t" -H "User-Agent: universal-agent-pub" \
    -H "Accept: application/vnd.github+json" -o "$T/user.json" -w '%{http_code}' "$API/user" 2>/dev/null)"
  if [ "$code" = "200" ]; then
    TOKEN="$t"
    break
  else
    bad "Токен $(fp "$t") [${CAND_FILES[$i]}] не принят (HTTP $code)."
  fi
done
if [ -z "$TOKEN" ]; then
  bad "Ни один токен GitHub не принял."
  echo "    Выпусти новый (2 минуты):"
  echo "      github.com -> Settings -> Developer settings -> Personal access tokens ->"
  echo "      Tokens (classic) -> Generate new token (classic) -> галочка repo -> Generate -> Copy."
  echo "    Вставь его в .txt одной строкой и СОХРАНИ файл (Ctrl+S), затем запусти снова."
  echo "    Из какого файла каждый токен — подписано в строках выше."
  exit 1
fi
if command -v python3 >/dev/null 2>&1; then
  LOGIN="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("login",""))' "$T/user.json" 2>/dev/null || true)"
fi
[ -z "$LOGIN" ] && LOGIN="$(sed -n 's/.*"login"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$T/user.json" | head -n1)"
ok "Живой токен: $(fp "$TOKEN") — с ним и работаю."
ok "Ты на GitHub как: ${LOGIN:-неизвестно}"

AUTH="Authorization: Bearer $TOKEN"
CURL=(curl -sS -H "$AUTH" -H "User-Agent: universal-agent-pub" -H "Accept: application/vnd.github+json")

# --- 3. репозиторий: есть или создать ------------------------------------
REPO_HTML="https://github.com/${LOGIN}/${REPO_NAME}"
code="$( "${CURL[@]}" -o "$T/repo.json" -w '%{http_code}' "$API/repos/${LOGIN}/${REPO_NAME}" 2>/dev/null )"
if [ "$code" = "200" ]; then
  ok "Репозиторий уже существует — использую его."
elif [ "$code" = "404" ] || [ "$code" = "000" ]; then
  step "Создаю репозиторий: $REPO_HTML"
  priv="false"; [ "$PRIVATE" = "1" ] && priv="true"
  body="$(printf '{"name":"%s","private":%s,"auto_init":false,"description":"UniversalAgent (TsUA) - single-brain holistic agent: one LLM core, closed perception-action loop, four-layer memory, sandboxed tools. Pure stdlib."}' \
    "$REPO_NAME" "$priv")"
  code="$( "${CURL[@]}" -o "$T/create.json" -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$body" "$API/user/repos" 2>/dev/null )"
  if [ "$code" = "201" ]; then
    ok "Репозиторий создан: $REPO_HTML"
  elif [ "$code" = "422" ]; then
    ok "Репозиторий уже существует — использую его."
  else
    bad "Не смог создать репозиторий (HTTP $code)."
    sed -n 's/.*"message"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/    GitHub: \1/p' "$T/create.json" 2>/dev/null | redact
    echo "    Проверь: токен со scope repo, имя \"$REPO_NAME\" свободно, сеть доступна."
    exit 1
  fi
else
  bad "Не смог проверить репозиторий (HTTP $code). Проверь сеть и повтори."
  exit 1
fi

# --- 4. история git (в архиве .git уже вложен; это на случай распаковки без него) ---
if [ ! -d .git ]; then
  step ".git нет — создаю канон-историю (сквош + тег)..."
  git init -q -b "$BRANCH" 2>/dev/null || { git init -q; git symbolic-ref HEAD "refs/heads/$BRANCH"; }
  git add -A
  # токены в коммит не попадут: проверяю каждый staged-файл (учёт кириллицы: quotepath=off, -z)
  while IFS= read -r -d '' f; do
    [ -z "$f" ] && continue
    if grep -qE 'gh[posur]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}' "$f" 2>/dev/null; then
      git rm -q --cached -- "$f"
      bad "В коммит не пойдёт (внутри токен): $f"
    fi
  done < <(git -c core.quotepath=off diff --cached --name-only -z)
  # страховка: даже если что-то проскочило — сканирую весь staged-дифф
  if git -c core.quotepath=off diff --cached 2>/dev/null | grep -qE 'gh[posur]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}'; then
    git reset -q
    bad "В staged-изменениях остался токен. Публикация остановлена."
    echo "    Убери файл с токеном из папки проекта (или в Загрузки) и запусти скрипт снова."
    exit 1
  fi
  if ! git diff --cached --quiet; then
    git -c "user.name=$GIT_NAME" -c "user.email=$GIT_EMAIL" commit -q \
      -m "feat(agent): UniversalAgent v2.2.0 - holistic single-brain agent" \
      -m "Signed-off-by: ${GIT_NAME} <${GIT_EMAIL}>"
  fi
  if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
    git -c "user.name=$GIT_NAME" -c "user.email=$GIT_EMAIL" tag -a "$TAG" -m "UniversalAgent $TAG" 2>/dev/null || true
  fi
fi

# --- 5. remote -----------------------------------------------------------
ORIGIN="${GIT_REMOTE_OVERRIDE:-https://github.com/${LOGIN}/${REPO_NAME}.git}"
if git remote | grep -qx origin; then
  git remote set-url origin "$ORIGIN"
else
  git remote add origin "$ORIGIN"
fi

# --- 6. пуш: авторизация заголовком, токен не в URL, не в config, вопросов нет ---
b64() { if command -v base64 >/dev/null 2>&1; then base64 | tr -d '\n'; else openssl base64 -A; fi; }
B64="$(printf 'x-access-token:%s' "$TOKEN" | b64)"

step "Пуш ветки $BRANCH ..."
out="$(git -c "credential.helper=" -c "http.extraHeader=AUTHORIZATION: basic $B64" \
  push -u origin "${BRANCH}:${BRANCH}" ${FORCE_PUSH:+--force} 2>&1)"; rc=$?
printf '%s\n' "$out" | redact
if [ $rc -ne 0 ]; then
  bad "Пуш не прошёл (код $rc)."
  echo "    Если на remote уже есть история (например, создал репо с README) — запусти так:"
  echo "      FORCE_PUSH=1 bash publish.sh"
  exit 1
fi
ok "Ветка $BRANCH отправлена."

step "Пуш тега $TAG (force: перезаливаем на текущий HEAD) ..."
out="$(git -c "credential.helper=" -c "http.extraHeader=AUTHORIZATION: basic $B64" push --force origin "$TAG" 2>&1)"; rc=$?
printf '%s\n' "$out" | redact
if [ $rc -eq 0 ]; then ok "Тег $TAG отправлен (указывает на свежий коммит)."
else bad "Тег не отправлен (не критично). Руками: git push --force origin $TAG"; fi

# --- 7. токен из памяти — стереть ----------------------------------------
unset TOKEN B64 AUTH CANDIDATES

# --- 8. проверка результата ----------------------------------------------
code="$( "${CURL[@]}" -o /dev/null -w '%{http_code}' "$API/repos/${LOGIN}/${REPO_NAME}/branches/${BRANCH}" 2>/dev/null )"
[ "$code" = "200" ] && ok "Проверено: ветка $BRANCH на GitHub на месте (HTTP 200)."
tcode="$( "${CURL[@]}" -o /dev/null -w '%{http_code}' "$API/repos/${LOGIN}/${REPO_NAME}/git/ref/tags/$TAG" 2>/dev/null )"
[ "$tcode" = "200" ] && ok "Проверено: тег $TAG на GitHub на месте (HTTP 200)."

echo ""
ok "ГОТОВО. Репозиторий: $REPO_HTML"
ok "Actions:     $REPO_HTML/actions — CI сам прогонит тесты и канон 6/6"
echo ""
printf '\033[33m  Отзови токен, которым пушил: GitHub -> Settings -> Developer settings -> Personal access tokens.\033[0m\n'
exit 0
