#!/bin/sh
# Shipped by HACS; run in a persistent Terminal/SSH add-on or HAOS host shell.
# Only GET commands: this recorder never restarts, reloads or changes HA.
set -u
umask 077

usage() {
    echo "Usage: sh capture_ha_memory.sh OUTPUT [SAMPLES=90] [INTERVAL=2] [ADDON_SLUG ...]" >&2
    exit 2
}

[ "$#" -ge 1 ] || usage
output=$1
samples=${2:-90}
interval=${3:-2}
case $samples in ''|*[!0-9]*) usage ;; esac
case $interval in ''|*[!0-9]*) usage ;; esac
[ "$samples" -ge 1 ] && [ "$samples" -le 900 ] || usage
[ "$interval" -ge 1 ] && [ "$interval" -le 60 ] || usage
if [ "$#" -ge 3 ]; then shift 3; else set --; fi
for slug in "$@"; do
    case $slug in ''|-*|*[!a-zA-Z0-9_-]*) usage ;; esac
done
for tool in ha timeout date sleep sed; do
    command -v "$tool" >/dev/null 2>&1 || { echo "Missing command: $tool" >&2; exit 2; }
done
# Refuse overwrite and symlinks; existing diagnostic evidence is preserved.
if ! (set -C; : > "$output") 2>/dev/null; then
    echo "Cannot create new output file: $output" >&2
    exit 2
fi

capture() {
    printf '\n--- %s %s ---\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"
    shift
    timeout 3 ha "$@" --raw-json 2>&1 || printf 'request_failed exit=%s\n' "$?"
}

(
    echo "battery_manager_memory_capture schema=1 samples=$samples interval_seconds=$interval"
    echo "proc_meminfo reflects the recorder's Linux namespace; Supervisor stats are container usage."
    capture core_info core info
    index=0
    while [ "$index" -lt "$samples" ]; do
        started=$(date +%s)
        printf '\n--- %s host_memory sample=%s ---\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$index"
        sed -n '/^MemTotal:/p; /^MemAvailable:/p; /^SwapTotal:/p; /^SwapFree:/p' /proc/meminfo
        capture core_stats core stats
        capture supervisor_stats supervisor stats
        for slug in "$@"; do capture "addon_stats:$slug" addons stats "$slug"; done
        index=$((index + 1))
        remaining=$((interval - $(date +%s) + started))
        # Slow/failed requests extend cadence; timestamps retain actual timing.
        if [ "$index" -lt "$samples" ] && [ "$remaining" -gt 0 ]; then sleep "$remaining"; fi
    done
    echo "capture_complete"
) >> "$output"
