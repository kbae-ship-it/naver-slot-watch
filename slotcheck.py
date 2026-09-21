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
    isUnitBusinessDay(진료시간)만 보면 안 되고 isUnitSaleDay(해당 시술 판매 시간)를
    반드시 함께 봐야 한다. 병원은 이 시술용으로 하루 1~6개 슬롯만 연다.
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
def ntfy_push(topic, slots, server="https://ntfy.sh", label="예약", email=None):
    """휴대폰 푸시. 알림을 누르면 해당 날짜 예약 페이지가 열린다."""
    if not topic:
        return False, "topic 없음"
    slots = sorted(slots)
    head = slots[0]
    day = head.split(" ")[0]
    extra = f"\n외 {len(slots) - 1}건: " + ", ".join(slots[1:6]) if len(slots) > 1 else ""
    payload = {
        "topic": topic,
        "title": f"🔔 {label} 빈자리",
        "message": f"{head}{extra}",
        "priority": 5,
        "tags": ["rotating_light"],
        "click": booking_url(day),
    }
    if email:
        payload["email"] = email
    req = urllib.request.Request(
        server.rstrip("/") + "/",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return (200 <= r.status < 300), f"HTTP {r.status}"
    except Exception as e:
        return False, str(e)


# ── 이메일 ───────────────────────────────────────────────────────────────────
def compose(slots, label="예약"):
    """알림 제목과 본문. 병원명·시술명은 넣지 않는다 (공개 경로를 지나므로)."""
    slots = sorted(slots)
    day = slots[0].split(" ")[0]
    subject = f"[{label}] 빈자리 {len(slots)}건 — {slots[0]}"
    lines = ["예약 빈자리가 생겼습니다.", ""]
    lines += [f"  · {s}" for s in slots]
    lines += ["", f"예약: {booking_url(day)}"]
    if len({s.split(" ")[0] for s in slots}) > 1:
        lines += ["", "날짜별 링크:"]
        lines += [f"  {d}: {booking_url(d)}" for d in sorted({s.split(" ")[0] for s in slots})]
    return subject, "\n".join(lines)


def send_smtp(slots, cfg, label="예약"):
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

    subject, body = compose(slots, label)
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


def alert(slots, topic="", email="", smtp_cfg=None, server="https://ntfy.sh", label="예약"):
    """푸시 + 이메일을 한 번에. SMTP가 설정돼 있으면 그쪽으로, 아니면 ntfy 경유.

    반환: [(경로이름, 성공여부, 메시지), ...]
    """
    results = []
    use_smtp = bool(smtp_cfg and smtp_cfg.get("host") and smtp_cfg.get("to"))

    if topic:
        ok, info = ntfy_push(topic, slots, server, label,
                             email=None if use_smtp else (email or None))
        results.append(("ntfy 푸시" + ("" if use_smtp else "+메일"), ok, info))

    if use_smtp:
        ok, info = send_smtp(slots, smtp_cfg, label)
        results.append(("SMTP 메일", ok, info))
    elif email and not topic:
        results.append(("메일", False, "ntfy 토픽이 없어 메일을 보낼 경로가 없습니다"))

    return results
