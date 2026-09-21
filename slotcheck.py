#!/usr/bin/env python3
"""네이버 예약 빈자리 판정 코어 — 로컬 감시기와 GitHub Actions가 공유."""

import json
import urllib.request
import datetime
from concurrent.futures import ThreadPoolExecutor

BUSINESS_ID = "597072"
BIZ_ITEM_ID = "6568346"
BUSINESS_TYPE_ID = 13
HORIZON_DAYS = 192
HEARTBEAT_DAYS = 3   # 변화가 없어도 이 주기로는 커밋해 저장소를 활성 상태로 유지

GRAPHQL = "https://m.booking.naver.com/graphql"
REFERER = f"https://m.booking.naver.com/booking/{BUSINESS_TYPE_ID}/bizes/{BUSINESS_ID}/items/{BIZ_ITEM_ID}"
HEADERS = {
    "Content-Type": "application/json",
    "Referer": REFERER,
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
}

Q_DAILY = "query schedule($p: ScheduleParams) { schedule(input: $p) { bizItemSchedule { daily { date } } } }"

Q_HOURLY = """query hourlySchedule($scheduleParams: ScheduleParams) {
  schedule(input: $scheduleParams) {
    bizItemSchedule {
      hourly {
        unitStartTime unitStock unitBookingCount
        stock bookingCount occupiedBookingCount
        isSaleDay isBusinessDay isUnitSaleDay isUnitBusinessDay isHoliday
      }
    }
  }
}"""


def gql(query, variables, op=None, timeout=20):
    payload = {"query": query, "variables": variables}
    if op:
        payload["operationName"] = op
    req = urllib.request.Request(GRAPHQL, data=json.dumps(payload).encode(), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    if "errors" in body:
        raise RuntimeError(json.dumps(body["errors"], ensure_ascii=False)[:300])
    return body["data"]


def booking_url(date):
    return (f"https://booking.naver.com/booking/{BUSINESS_TYPE_ID}/bizes/{BUSINESS_ID}"
            f"/items/{BIZ_ITEM_ID}?startDate={date}&theme=place")


def slot_availability(s):
    """네이버 프론트엔드 판정식과 동일.

    가능 = isSaleDay && isBusinessDay && isUnitBusinessDay && isUnitSaleDay && !isHoliday
           && min(일단위 잔여, unitStock - unitBookingCount) >= 1
    isUnitBusinessDay(영업시간)만 보면 안 되고 isUnitSaleDay(해당 상품 판매 시간)를
    반드시 함께 봐야 한다. 업체는 상품별로 일부 슬롯만 판매용으로 연다.
    """
    if not (s.get("isSaleDay") and s.get("isBusinessDay")
            and s.get("isUnitBusinessDay") and s.get("isUnitSaleDay")):
        return 0
    if s.get("isHoliday"):
        return 0
    stock = s.get("stock")
    unit_stock = s.get("unitStock") or 0
    if stock is None:
        day_left = unit_stock
    else:
        day_left = stock - (s.get("bookingCount") or 0) - (s.get("occupiedBookingCount") or 0)
    return min(day_left, unit_stock - (s.get("unitBookingCount") or 0))


def date_window():
    today = datetime.date.today()
    return today, today + datetime.timedelta(days=HORIZON_DAYS)


def fetch_daily():
    a, b = date_window()
    data = gql(Q_DAILY, {"p": {
        "businessId": BUSINESS_ID, "businessTypeId": BUSINESS_TYPE_ID, "bizItemId": BIZ_ITEM_ID,
        "startDateTime": f"{a}T00:00:00", "endDateTime": f"{b}T23:59:59",
    }})
    days = (data["schedule"]["bizItemSchedule"]["daily"] or {}).get("date") or {}
    return {
        d: (v.get("bookingCount"), v.get("occupiedBookingCount"), v.get("stock"),
            bool(v.get("isSaleDay")), bool(v.get("isBusinessDay")), bool(v.get("isHoliday")))
        for d, v in days.items()
    }


def sale_days(daily):
    return sorted(d for d, v in daily.items() if v[3] and v[4] and not v[5])


def fetch_hourly(day):
    try:
        data = gql(Q_HOURLY, {"scheduleParams": {
            "businessId": BUSINESS_ID, "businessTypeId": BUSINESS_TYPE_ID, "bizItemId": BIZ_ITEM_ID,
            "startDateTime": f"{day}T00:00:00", "endDateTime": f"{day}T23:59:59",
        }}, op="hourlySchedule")
        return day, (data["schedule"]["bizItemSchedule"]["hourly"] or [])
    except Exception:
        return day, None


def open_slots_for(days, workers=6):
    """여러 날짜를 병렬 조회 → (예약가능 슬롯 집합, 조회실패 날짜)."""
    found, failed = set(), []
    if not days:
        return found, failed
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for day, rows in ex.map(fetch_hourly, days):
            if rows is None:
                failed.append(day)
                continue
            for s in rows:
                ts = s.get("unitStartTime")
                if not ts or not ts.startswith(day):
                    continue
                if slot_availability(s) >= 1:
                    found.add(f"{day} {ts.split(' ')[1][:5]}")
    return found, failed


def scan_all():
    """전체 판매일을 훑어 예약 가능한 슬롯 집합을 반환."""
    daily = fetch_daily()
    days = sale_days(daily)
    found, failed = open_slots_for(days)
    return found, days, failed


# ── ntfy 푸시 ────────────────────────────────────────────────────────────────
TEST_SLOT = "9999-12-31 00:00"   # 테스트 전용. 실제 달력에 존재할 수 없는 값.


def ntfy_push(topic, slots, server="https://ntfy.sh", label="예약",
              email=None, token=None, test=False):
    """휴대폰 푸시. 알림을 누르면 해당 날짜 예약 페이지가 열린다.

    email 은 ntfy 계정 토큰이 있을 때만 싣는다. ntfy.sh 는 익명 이메일 발송을
    거부하며(40053), 그 경우 요청 전체가 400으로 실패해 푸시까지 날아간다.
    푸시는 어떤 경우에도 살아남아야 하므로 토큰이 없으면 email 을 뺀다.
    """
    if not topic:
        return False, "topic 없음"
    slots = sorted(slots)
    head = slots[0]
    day = head.split(" ")[0]
    extra = f"\n외 {len(slots) - 1}건: " + ", ".join(slots[1:6]) if len(slots) > 1 else ""
    if test:
        payload = {
            "topic": topic,
            "title": "🧪 테스트 — 실제 빈자리 아님",
            "message": "알림 경로 점검용입니다. 예약 페이지에 가실 필요 없습니다.",
            "priority": 3,
            "tags": ["test_tube"],
        }
    else:
        payload = {
            "topic": topic,
            "title": f"🔔 {label} 빈자리",
            "message": f"{head}{extra}",
            "priority": 5,
            "tags": ["rotating_light"],
            "click": booking_url(day),
        }
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if email and token:
        payload["email"] = email
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        server.rstrip("/") + "/",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return (200 <= r.status < 300), f"HTTP {r.status}"
    except Exception as e:
        return False, str(e)


# ── 이메일 ───────────────────────────────────────────────────────────────────
def compose(slots, label="예약", test=False):
    """알림 제목과 본문. 업체명·상품명은 넣지 않는다 (공개 경로를 지나므로)."""
    slots = sorted(slots)
    day = slots[0].split(" ")[0]
    if test:
        return ("[테스트] 알림 경로 점검 — 실제 빈자리 아님",
                "알림이 정상 동작하는지 확인하는 메일입니다.\n"
                "실제 빈자리가 아니니 예약 페이지에 가실 필요 없습니다.")
    subject = f"[{label}] 빈자리 {len(slots)}건 — {slots[0]}"
    lines = ["예약 빈자리가 생겼습니다.", ""]
    lines += [f"  · {s}" for s in slots]
    lines += ["", f"예약: {booking_url(day)}"]
    if len({s.split(" ")[0] for s in slots}) > 1:
        lines += ["", "날짜별 링크:"]
        lines += [f"  {d}: {booking_url(d)}" for d in sorted({s.split(" ")[0] for s in slots})]
    return subject, "\n".join(lines)


def send_smtp(slots, cfg, label="예약", test=False):
    """SMTP 직접 발송. cfg = dict(host, port, user, password, sender, to).

    ntfy 무료 티어의 이메일 발송 제한에 걸릴 때를 대비한 경로.
    """
    import smtplib
    import ssl
    from email.message import EmailMessage

    need = ("host", "user", "password", "to")
    missing = [k for k in need if not cfg.get(k)]
    if missing:
        return False, f"설정 없음: {', '.join(missing)}"

    subject, body = compose(slots, label, test)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("sender") or cfg["user"]
    msg["To"] = cfg["to"]
    msg.set_content(body)

    port = int(cfg.get("port") or 587)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, context=ssl.create_default_context(),
                                  timeout=20) as sm:
                sm.login(cfg["user"], cfg["password"])
                sm.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=20) as sm:
                sm.starttls(context=ssl.create_default_context())
                sm.login(cfg["user"], cfg["password"])
                sm.send_message(msg)
        return True, f"{cfg['to']} 로 발송"
    except Exception as e:
        return False, str(e)


def smtp_config_from_env(env):
    return {
        "host": env.get("SMTP_HOST", "").strip(),
        "port": env.get("SMTP_PORT", "").strip(),
        "user": env.get("SMTP_USER", "").strip(),
        "password": env.get("SMTP_PASS", "").strip(),
        "sender": env.get("SMTP_FROM", "").strip(),
        "to": env.get("ALERT_EMAIL", "").strip(),
    }


def alert(slots, topic="", email="", smtp_cfg=None, server="https://ntfy.sh",
          label="예약", ntfy_token="", test=False):
    """푸시와 이메일을 각각 독립적으로 보낸다. 한쪽이 실패해도 다른 쪽은 간다.

    이메일 경로 우선순위:
      1) SMTP 설정이 있으면 SMTP 직접 발송
      2) 없고 ntfy 토큰이 있으면 ntfy 경유
      3) 둘 다 없으면 이메일은 보내지 못한다고 명시적으로 보고
    반환: [(경로이름, 성공여부, 메시지), ...]
    """
    results = []
    smtp_cfg = smtp_cfg or {}
    use_smtp = bool(smtp_cfg.get("host") and smtp_cfg.get("to"))
    use_ntfy_mail = bool(email and ntfy_token and not use_smtp)

    if topic:
        ok, info = ntfy_push(topic, slots, server, label,
                             email=email if use_ntfy_mail else None,
                             token=ntfy_token if use_ntfy_mail else None,
                             test=test)
        results.append(("ntfy 푸시" + ("+메일" if use_ntfy_mail else ""), ok, info))

    if use_smtp:
        results.append(("SMTP 메일",) + send_smtp(slots, smtp_cfg, label, test))
    elif email and not use_ntfy_mail:
        # 설정을 안 한 것이지 고장난 게 아니다. 호출부가 구분할 수 있게 이름으로 표시한다.
        results.append(("이메일(미설정)", False,
                        "SMTP_HOST 를 설정하거나 NTFY_TOKEN 이 필요합니다"))

    return results
