#!/bin/sh
# Runs only inside the private filesystem/PID namespace created by the launcher.
set -eu
umask 077
mkdir -p /session/profile/Default
cat > /session/profile/Default/Preferences <<'EOF'
{"credentials_enable_service":false,"profile":{"password_manager_enabled":false},"translate":{"enabled":false}}
EOF
# No TCP or abstract Unix X socket. The filesystem socket is in private /tmp.
Xvfb :99 -screen 0 480x800x24 -nolisten tcp -nolisten local -ac -displayfd 5 \
  5>/session/display 3<&- 4>&- >/dev/null 2>&1 &
display_pid=$!
attempt=0
while [ ! -s /session/display ]; do
  kill -0 "$display_pid"
  attempt=$((attempt + 1))
  [ "$attempt" -lt 100 ]
  sleep 0.05
done
exec /usr/lib/chromium/chromium \
  --remote-debugging-pipe --user-data-dir=/session/profile \
  --no-first-run --no-default-browser-check --disable-sync \
  --disable-background-networking --disable-component-update --disable-default-apps \
  --disable-extensions --disable-gpu --disable-dev-shm-usage --password-store=basic \
  --window-size=480,800 --ozone-platform=x11 about:blank
