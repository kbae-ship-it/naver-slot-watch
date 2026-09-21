#!/usr/bin/env python3
"""GitHub Actions용 1회 점검. 이전 상태와 비교해 새 빈자리가 생겼으면 ntfy로 푸시.

환경변수:
  NTFY_TOPIC   (필수) ntfy 토픽 이름
  ALERT_EMAIL  (선택) 알림 받을 이메일 주소
  NTFY_SERVER  (선택) 기본 https://ntfy.sh
  ALERT_LABEL  (선택) 알림 제목에 들어갈 이름. 기본 "예약"
  NTFY_TOKEN   (선택) ntfy 계정 토큰. 있어야 ntfy 경유 메일이 나갑니다
  SMTP_HOST/PORT/USER/PASS/FROM (선택) 설정하면 ntfy 대신 SMTP로 직접 발송
"""

import json
import os
import sys
import datetime

import slotcheck

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip()
    label = os.environ.get("ALERT_LABEL", "예약").strip() or "예약"
    email = os.environ.get("ALERT_EMAIL", "").strip()
    smtp = slotcheck.smtp_config_from_env(os.environ)

    def fire(slots):
        results = slotcheck.alert(slots, topic=topic, email=email,
                                  smtp_cfg=smtp, server=server, label=label,
                                  ntfy_token=os.environ.get("NTFY_TOKEN", "").strip())
        for name, ok, info in results:
            print(f"{name}: {'성공' if ok else '실패'} ({info})")
        return all(ok for _, ok, _ in results) if results else False

    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        prev = set(st.get("available", []))
        first_run = False
    except Exception:
        prev, first_run = set(), True

    try:
        found, days, failed = slotcheck.scan_all()
    except Exception as e:
        print(f"::warning::조회 실패, 이번 실행 건너뜀 — {e}")
        return 0

    # 조회에 실패한 날짜는 판단 불가 → 이전 상태를 그대로 유지 (거짓 알림 방지)
    fset = set(failed)
    current = found | {s for s in prev if s.split(" ")[0] in fset}
    gained = current - prev

    print(f"판매일 {len(days)}일 / 예약가능 {len(current)}개"
          + (f" / 조회실패 {len(failed)}일" if failed else ""))
    for s in sorted(current):
        print(f"  가능: {s}")

    if first_run:
        print("첫 실행 — 기준선을 저장합니다.")
        if current:
            print("첫 실행 시점에 이미 빈자리가 있습니다.")
            fire(current)
    elif gained:
        print(f"::notice::빈자리 {len(gained)}개 — {', '.join(sorted(gained))}")
        if not fire(gained):
            print("::error::알림 발송에 실패한 경로가 있습니다")
    else:
        print("새 빈자리 없음.")

    changed = current != prev
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"available": sorted(current),
                   "checked": datetime.datetime.now(datetime.timezone.utc).isoformat()},
                  f, ensure_ascii=False, indent=1)
        f.write("\n")

    # 워크플로가 커밋 여부를 판단할 수 있게 출력
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
