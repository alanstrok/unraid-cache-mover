#!/bin/bash
#
# cache_webhook.sh {start|stop|restart|status}
#
# Supervises the Plex webhook listener (plex_webhook.py) which triggers
# head-start fills the instant playback begins. Reads connection details
# from the plugin settings.
#
source /boot/config/plugins/cache-mover/cachemoversettings

listener="/usr/local/emhttp/plugins/cache-mover/scripts/plex_webhook.py"
pidfile="/var/run/cache_mover_webhook.pid"
logfile="/var/log/cache_mover_webhook.log"

webhook_port="${webhook_port:-8595}"
mediaserver_url="${mediaserver_url:-http}"

is_running() {
    [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

start() {
    if is_running; then
        echo "webhook listener already running (pid $(cat "$pidfile"))"
        return 0
    fi
    if [ -z "$mediaserver_host" ] || [ -z "$mediaserver_port" ] || [ -z "$mediaserver_key" ]; then
        echo "mediaserver host/port/key not set, cannot start webhook listener"
        return 1
    fi
    nohup python3 "$listener" \
        --host "$mediaserver_host" --port "$mediaserver_port" \
        --scheme "$mediaserver_url" --token "$mediaserver_key" \
        --listen-port "$webhook_port" >"$logfile" 2>&1 &
    echo $! > "$pidfile"
    echo "started webhook listener pid $(cat "$pidfile") on port $webhook_port"
}

stop() {
    if is_running; then
        kill "$(cat "$pidfile")" 2>/dev/null
        rm -f "$pidfile"
        echo "stopped webhook listener"
    else
        rm -f "$pidfile"
        echo "webhook listener not running"
    fi
}

case "$1" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  if is_running; then echo "running (pid $(cat "$pidfile"))"; else echo "stopped"; fi ;;
    *)       echo "usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
