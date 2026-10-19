#!/bin/bash
# 빈자리 감시 제어 — 맥 감시기와 GitHub 양쪽을 한 번에.
#
#   ./watch.sh status   현재 상태
#   ./watch.sh stop     멈춤 (= pause)
#   ./watch.sh start    재개 (= resume)
#   ./watch.sh now      지금 현황만 조회
#   ./watch.sh test     알림 점검 (🧪 표시, 실제 빈자리 아님)

cd "$(dirname "$0")" || exit 1
CMD="${1:-status}"

running() { pgrep -f watch_local.py >/dev/null; }
caf()     { pgrep -x caffeinate   >/dev/null; }

github_flag() {   # $1 = on|off  — 저장소의 PAUSED 파일로 GitHub 쪽 알림을 제어
  if [ "$1" = "on" ]; then date -u '+%Y-%m-%dT%H:%M:%SZ' > PAUSED; git add -f PAUSED >/dev/null 2>&1
  else rm -f PAUSED; git rm -q --cached PAUSED >/dev/null 2>&1; fi
  if git diff --cached --quiet 2>/dev/null; then return 0; fi
  git -c user.email=slot@local -c user.name=watch commit -qm "감시 $([ "$1" = on ] && echo 일시중지 || echo 재개)" >/dev/null 2>&1
  # 워크플로가 state.json 을 계속 커밋하므로 원격이 앞서 있는 게 정상. 먼저 받아온다.
  git -c user.email=slot@local -c user.name=watch pull -q --rebase --autostash origin main >/dev/null 2>&1
  if git push -q origin main 2>/dev/null; then echo "  GitHub: $([ "$1" = on ] && echo 알림 중지 || echo 알림 재개)"
  else echo "  GitHub: ★ 푸시 실패. 맥 쪽만 반영됐습니다 — ./watch.sh status 로 확인하세요."; fi
}

case "$CMD" in
  status)
    echo "시각: $(date '+%Y-%m-%d %H:%M:%S')"
    if running; then P=$(pgrep -f watch_local.py|head -1)
      echo "  감시기   : 실행 중 (PID $P, 가동 $(ps -o etime= -p "$P"|tr -d ' '))"
    else echo "  감시기   : 멈춤"; fi
    caf && echo "  잠자기차단: 작동 중" || echo "  잠자기차단: 꺼짐"
    [ -f PAUSED ] && echo "  GitHub   : 일시중지 ($(cat PAUSED))" || echo "  GitHub   : 알림 켜짐"
    [ -f slot_watch.log ] && echo "  마지막확인: $(tail -1 slot_watch.log | cut -d']' -f1 | tr -d '[')"
    ;;

  stop|pause)
    pkill -f watch_local.py 2>/dev/null; pkill -x caffeinate 2>/dev/null; sleep 1
    running && echo "  ★ 감시기가 아직 살아 있습니다" || echo "  감시기: 멈춤"
    echo "  잠자기차단: 해제"
    github_flag on
    echo
    echo "알림이 멈췄습니다. 다시 켜려면: ./watch.sh start"
    ;;

  start|resume)
    if running; then echo "  이미 실행 중입니다 (PID $(pgrep -f watch_local.py|head -1))"
    else
      echo "  지금 열려 있는 자리를 먼저 확인합니다..."
      python3 watch_local.py --once 2>&1 | sed -n '3,12p' | sed 's/^/    /'
      # 재개 직후 그동안 쌓인 자리가 한꺼번에 알림으로 터지지 않게 기준선을 새로 잡는다
      rm -f local_state.json
      nohup python3 watch_local.py > watcher.out 2>&1 &
      sleep 4
      running && echo "  감시기: 시작됨 (PID $(pgrep -f watch_local.py|head -1))" || { echo "  ★ 시작 실패 — watcher.out 확인"; exit 1; }
      nohup caffeinate -is -w "$(pgrep -f watch_local.py|head -1)" >/dev/null 2>&1 &
      sleep 1; caf && echo "  잠자기차단: 작동" || echo "  잠자기차단: ★ 실패"
    fi
    github_flag off
    echo
    echo "감시를 재개했습니다. 위에 나온 자리는 이미 열려 있던 것이라 알림이 가지 않습니다."
    ;;

  now)   python3 watch_local.py --once ;;
  test)  python3 watch_local.py --test ;;

  *) echo "사용법: ./watch.sh [status|stop|start|now|test]"; exit 1 ;;
esac
