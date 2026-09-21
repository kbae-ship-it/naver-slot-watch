#!/usr/bin/env python3
"""맥에서 도는 빠른 감시기 (20초 간격).

맥이 깨어 있을 때 쓰는 1차 감시. 맥이 잠들면 GitHub Actions가 5분 간격으로 대신 봅니다.
빈자리 발견 시: macOS 알림 + 한국어 음성 + 휴대폰 ntfy 푸시 + 예약 페이지 자동 열기.

토픽 설정 (둘 중 하나):
  export NTFY_TOPIC=...            또는
  echo '토픽이름' > .ntfy_topic

사용법:
  python3 watch_local.py            # 감시 시작
  python3 watch_local.py --once     # 현황만 출력
  python3 watch_local.py --test     # 알림 경로 점검 (실제 빈자리 아님)
  python3 watch_local.py --no-open  # 브라우저 자동 열기 끄기
"""

import datetime
import json
import os
import subprocess
import sys
import time

import slotcheck

LABEL = os.environ.get("ALERT_LABEL", "예약")
FAST_INTERVAL = 20
FULL_SWEEP_INTERVAL = 300
OPEN_COOLDOWN = 60

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "local_state.json")
LOG_FILE = os.path.join(HERE, "slot_watch.log")
TOPIC_FILE = os.path.join(HERE, ".ntfy_topic")
EMAIL_FILE = os.path.join(HERE, ".alert_email")
TOKEN_FILE = os.path.join(HERE, ".ntfy_token")
ENV_FILE = os.path.join(HERE, ".env.local")


def load_env_file():
    """.env.local 의 KEY=VALUE 를 환경변수로 읽어들인다.

    nohup 으로 띄운 프로세스는 셸에서 export 한 값을 물려받지 못하므로
    SMTP 설정 같은 건 파일에서 읽어야 한다. 이미 설정된 환경변수가 우선.
    """
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


load_env_file()


def ntfy_topic():
    t = os.environ.get("NTFY_TOPIC", "").strip()
    if t:
        return t
    try:
        with open(TOPIC_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _read(path, env_key):
    v = os.environ.get(env_key, "").strip()
    if v:
        return v
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def ntfy_token():
    return _read(TOKEN_FILE, "NTFY_TOKEN")


def alert_email():
    e = os.environ.get("ALERT_EMAIL", "").strip()
    if e:
        return e
    try:
        with open(EMAIL_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


_last_open = 0.0


def notify(new_slots, auto_open=True):
    global _last_open
    slots = sorted(new_slots)
    head = slots[0]
    day = head.split(" ")[0]
    extra = f" 외 {len(slots) - 1}건" if len(slots) > 1 else ""
    body = f"{head}{extra}"

    def osa(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{osa(body)}" with title "🔔 {osa(LABEL)} 빈자리" '
                        f'sound name "Glass"'], check=False, timeout=10)
    except Exception as e:
        log(f"  ! macOS 알림 실패: {e}")

    try:
        hh, mm = head.split(" ")[1].split(":")
        md = day.split("-")
        subprocess.Popen(["say", "-v", "Yuna",
                          f"빈자리가 났습니다. {int(md[1])}월 {int(md[2])}일 {int(hh)}시 {int(mm)}분."])
    except Exception:
        try:
            subprocess.Popen(["say", "빈자리가 났습니다"])
        except Exception:
            pass

    for name, ok, info in slotcheck.alert(
            slots,
            topic=ntfy_topic(),
            email=alert_email(),
            smtp_cfg=slotcheck.smtp_config_from_env(
                dict(os.environ, ALERT_EMAIL=alert_email())),
            label=LABEL,
            ntfy_token=ntfy_token()):
        log(f"  {name}: {'성공' if ok else '실패'} ({info})")

    log(f"*** 빈자리: {', '.join(slots)}")
    log(f"    예약: {slotcheck.booking_url(day)}")

    if auto_open and time.time() - _last_open > OPEN_COOLDOWN:
        _last_open = time.time()
        try:
            subprocess.Popen(["open", slotcheck.booking_url(day)])
        except Exception as e:
            log(f"  ! 브라우저 열기 실패: {e}")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        return set(st.get("available", [])), st.get("daily") or {}
    except Exception:
        return None, {}


def save_state(available, daily):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"available": sorted(available), "daily": daily,
                   "saved": datetime.datetime.now().isoformat()}, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def main():
    args = set(sys.argv[1:])
    auto_open = "--no-open" not in args

    if "--test" in args:
        log("알림 경로 점검 — 실제 빈자리가 아닙니다.")
        notify({"2026-10-31 14:30"}, auto_open=False)
        return

    topic = ntfy_topic()
    log(f"감시 시작 — 빠른확인 {FAST_INTERVAL}초 / 전체스윕 {FULL_SWEEP_INTERVAL}초")
    mail = alert_email()
    smtp = slotcheck.smtp_config_from_env(os.environ)
    if not mail:
        route = "미설정"
    elif smtp.get("host"):
        route = f"{mail} (SMTP 직접발송)"
    elif ntfy_token():
        route = f"{mail} (ntfy 경유)"
        
    else:
        route = f"{mail} — 발송 경로 없음! SMTP_HOST 또는 NTFY_TOKEN 필요"
    log(f"휴대폰 푸시: {'토픽 설정됨' if topic else '미설정'} / 이메일: {route}")

    daily = slotcheck.fetch_daily()
    days = slotcheck.sale_days(daily)
    available, failed = slotcheck.open_slots_for(days)
    log(f"판매일 {len(days)}일 조회 완료. 예약 가능 슬롯 {len(available)}개"
        + (f" (조회실패 {len(failed)}일)" if failed else ""))

    if "--once" in args:
        for s in sorted(available):
            log(f"  가능: {s}")
        if not available:
            log("  전부 마감.")
        return

    prev_available, _ = load_state()
    if prev_available is None:
        prev_available = set(available)
        if available:
            log("첫 실행 시점에 이미 빈자리가 있습니다.")
            notify(available, auto_open)
    elif available - prev_available:
        notify(available - prev_available, auto_open)

    prev_available = set(available)
    prev_daily = daily
    save_state(prev_available, prev_daily)

    last_sweep = time.time()
    fails = 0

    while True:
        time.sleep(FAST_INTERVAL)
        try:
            now = time.time()
            daily = slotcheck.fetch_daily()
            if now - last_sweep >= FULL_SWEEP_INTERVAL:
                targets = slotcheck.sale_days(daily)
                last_sweep = now
                scope = "전체스윕"
            else:
                changed = [d for d, v in daily.items() if prev_daily.get(d) != v]
                targets = [d for d in changed if daily[d][3] and daily[d][4] and not daily[d][5]]
                scope = f"변화 {len(changed)}일" if changed else None

            if targets:
                found, failed = slotcheck.open_slots_for(targets)
                checked = set(targets) - set(failed)
                kept = {s for s in prev_available if s.split(" ")[0] not in checked}
                current = kept | found
            else:
                current = prev_available

            gained = current - prev_available
            if gained:
                notify(gained, auto_open)
            elif scope and targets:
                log(f"{scope} → 변동 확인, 새 빈자리 없음")

            prev_available, prev_daily = current, daily
            save_state(prev_available, prev_daily)
            fails = 0
        except KeyboardInterrupt:
            log("감시 중단.")
            return
        except Exception as e:
            fails += 1
            log(f"오류({fails}): {e}")
            time.sleep(min(300, FAST_INTERVAL * (2 ** min(fails, 4))))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("감시 중단.")
