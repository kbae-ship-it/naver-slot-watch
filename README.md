# 네이버 예약 빈자리 감시

자연튼튼의원 · 편평사마귀 제거 · 편사1시간30분여유(최소15만원)
`businessId=597072`, `bizItemId=6568346`

취소로 자리가 나면 휴대폰으로 즉시 푸시. 알림을 누르면 해당 날짜 예약 페이지가 열립니다.

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
> 알림 본문에는 병원명이나 시술명을 넣지 않고 날짜·시간만 보냅니다.

### 2. GitHub Actions

```bash
git init && git add -A && git commit -m "초기 설정"
# GitHub에서 빈 저장소를 만든 뒤 (Private 권장)
git remote add origin git@github.com:<사용자명>/<저장소명>.git
git branch -M main && git push -u origin main
```

저장소 → Settings → Secrets and variables → Actions:

- **New repository secret** → 이름 `NTFY_TOPIC`, 값에 토픽 이름
- (선택) **Variables** 탭 → `ALERT_LABEL` — 알림 제목에 쓸 이름. 기본 `예약`

Actions 탭 → `빈자리 감시` → **Run workflow** 로 한 번 수동 실행해서 확인.

### 3. 맥 감시기

```bash
echo '<토픽 이름>' > .ntfy_topic
nohup python3 watch_local.py > watcher.out 2>&1 &
```

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

`isUnitBusinessDay`(병원 진료시간)와 `isUnitSaleDay`(이 시술을 파는 시간)는 다릅니다.
진료시간은 09:00~21:00 전부 true지만, 병원은 이 시술용으로 하루 1~6개 슬롯만 엽니다.
**둘 다 봐야 합니다.** 앞의 것만 보면 마감된 날을 예약 가능으로 착각합니다.

## 알아둘 것

- GitHub Actions의 `*/5` cron은 최소 간격이 5분이고, 러너가 붐비면 몇 분 더 밀립니다.
  정확히 5분마다는 아닙니다.
- 공개 저장소에서 60일간 커밋이 없으면 GitHub이 예약 워크플로를 자동으로 끕니다.
  상태 변화가 있을 때마다 커밋이 생기므로 대개 문제되지 않지만, 조용한 기간이 길면
  Actions 탭에서 다시 켜주세요.
- 조회에 실패한 날짜는 이전 상태를 그대로 유지합니다. 일시적 오류로 거짓 알림이
  가지 않게 하려는 것입니다.
- 감시 범위는 오늘부터 192일입니다. 새 예약 기간이 열리면 자동으로 들어옵니다.
