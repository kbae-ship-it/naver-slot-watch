# 네이버 예약 빈자리 감시

네이버 예약에서 특정 상품의 취소 자리를 감시합니다.
감시 대상은 `slotcheck.py` 상단의 `BUSINESS_ID` / `BIZ_ITEM_ID` 로 지정합니다.

취소로 자리가 나면 **휴대폰 푸시 + 이메일**. 푸시를 누르면 해당 날짜 예약 페이지가 열립니다.

## 2중 감시

| | 어디서 | 간격 | 맥이 잠들면 |
|---|---|---|---|
| 1차 | 내 맥 (`watch_local.py`) | 20초 | 멈춤 |
| 2차 | GitHub Actions (`ci_check.py`) | 5분 | **계속 동작** |

두 쪽 다 같은 판정 코어(`slotcheck.py`)를 씁니다.

## 설치

### 1. ntfy 앱 (휴대폰)

App Store / Play 스토어에서 **ntfy** 설치 → `+` → 토픽 이름 입력 → 구독.
회원가입 없습니다.

> 토픽 이름은 비밀번호나 마찬가지입니다. ntfy.sh는 토픽 이름만 알면 누구나
> 구독할 수 있는 공개 서비스라, 추측 불가능한 무작위 이름을 써야 합니다.
> 알림 본문에는 업체명이나 상품명을 넣지 않고 날짜·시간만 보냅니다.

### 2. 이메일

받을 주소를 정합니다.

```bash
echo 'you@example.com' > .alert_email
```

그리고 **발송 경로**가 하나 필요합니다. ntfy.sh는 2024년부터 익명 이메일 발송을
막았기 때문에(`40053 anonymous email sending is not allowed`), 둘 중 하나를
설정해야 메일이 나갑니다.

**방법 A — SMTP 직접 발송 (권장)**

`.env.local.example` 을 `.env.local` 로 복사하고 값을 채웁니다.
Gmail이면 2단계 인증을 켜고 [앱 비밀번호](https://myaccount.google.com/apppasswords)를
발급해서 쓰세요. 일반 계정 비밀번호로는 로그인되지 않습니다.

| 이름 | 예시 |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` (SSL이면 `465`) |
| `SMTP_USER` | 보내는 계정 |
| `SMTP_PASS` | 앱 비밀번호 |
| `SMTP_FROM` | 생략하면 `SMTP_USER` |

**방법 B — ntfy 계정 경유**

ntfy.sh에 가입해 토큰(`tk_...`)을 발급받아 `.ntfy_token` 에 넣거나
`NTFY_TOKEN` 으로 설정합니다. 발송량 제한이 있습니다.

> `SMTP_HOST`가 설정되면 ntfy 경유 메일은 자동으로 꺼지고 SMTP만 씁니다.
> 둘 다 없으면 메일은 나가지 않지만 **푸시는 정상 동작합니다.**

### 3. GitHub Actions

```bash
git init && git add -A && git commit -m "초기 설정"
# GitHub에서 빈 저장소를 만든 뒤 (Private 권장)
git remote add origin git@github.com:<사용자명>/<저장소명>.git
git branch -M main && git push -u origin main
```

저장소 → Settings → Secrets and variables → Actions:

- **New repository secret** → `NTFY_TOPIC` — 토픽 이름
- **New repository secret** → `ALERT_EMAIL` — 알림 받을 이메일 주소
- 메일 발송 경로: `SMTP_*` 전부, 또는 `NTFY_TOKEN`
- (선택) **Variables** 탭 → `ALERT_LABEL` — 알림 제목에 쓸 이름. 기본 `예약`
- (선택) SMTP 직접 발송을 쓸 경우 `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM`

Actions 탭 → `빈자리 감시` → **Run workflow**.
`테스트 알림 보내기` 를 체크하면 🧪 표시가 붙은 점검용 알림이 실제로 발송됩니다.
진짜 빈자리 알림과 달리 예약 링크가 없고 우선순위가 낮습니다.

### 4. 맥 감시기

```bash
echo '<토픽 이름>' > .ntfy_topic
echo '<이메일 주소>' > .alert_email
cp .env.local.example .env.local   # 메일 발송 경로를 쓸 경우
nohup python3 watch_local.py > watcher.out 2>&1 &
```

시작 로그에 이메일 경로가 표시됩니다. `발송 경로 없음!` 이 보이면 메일은 안 갑니다.

## 명령

```bash
python3 watch_local.py --once     # 지금 현황만 출력
python3 watch_local.py --test     # 알림이 실제로 오는지 점검
python3 watch_local.py --no-open  # 브라우저 자동 열기 끄기
pkill -f watch_local.py           # 중단
tail -f slot_watch.log            # 로그 보기
```

## 빈자리 판정

네이버 프론트엔드 번들에서 그대로 옮겼습니다:

```
가능 = isSaleDay && isBusinessDay && isUnitBusinessDay && isUnitSaleDay && !isHoliday
       && min(일단위 잔여, unitStock - unitBookingCount) >= 1
```

`isUnitBusinessDay`(업체 영업시간)와 `isUnitSaleDay`(이 상품을 파는 시간)는 다릅니다.
영업시간은 하루 전체가 true여도, 업체는 상품별로 일부 슬롯만 판매용으로 엽니다.
**둘 다 봐야 합니다.** 앞의 것만 보면 마감된 날을 예약 가능으로 착각합니다.

## 알아둘 것

- GitHub Actions의 `*/5` cron은 최소 간격이 5분이고, 러너가 붐비면 몇 분 더 밀립니다.
  정확히 5분마다는 아닙니다.
- 공개 저장소에서 60일간 커밋이 없으면 GitHub이 예약 워크플로를 자동으로 끕니다.
  상태 변화가 있을 때마다 커밋이 생기므로 대개 문제되지 않지만, 조용한 기간이 길면
  Actions 탭에서 다시 켜주세요.
- 미등록 시크릿은 GitHub Actions 에서 **빈 문자열**로 전달됩니다. `os.environ.get(k, 기본값)`
  의 기본값이 적용되지 않으므로 `os.environ.get(k, "") or 기본값` 으로 받아야 합니다.
  이걸 놓치면 알림 경로 전체가 조용히 죽습니다.
- 조회에 실패한 날짜는 이전 상태를 그대로 유지합니다. 일시적 오류로 거짓 알림이
  가지 않게 하려는 것입니다.
- 감시 범위는 오늘부터 192일입니다. 새 예약 기간이 열리면 자동으로 들어옵니다.
- 알림 본문에는 업체명·상품명을 넣지 않고 날짜·시간과 예약 링크만 보냅니다.
  ntfy.sh 토픽은 이름만 알면 누구나 구독할 수 있는 공개 경로이기 때문입니다.
- 푸시와 이메일은 서로 독립적으로 발송합니다. 메일 설정이 잘못돼 있어도
  푸시는 정상적으로 갑니다.
- `.ntfy_topic` `.alert_email` `.ntfy_token` `.env.local` 은 모두 `.gitignore`
  대상이라 저장소에 올라가지 않습니다.
