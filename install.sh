#!/usr/bin/env bash
set -euo pipefail

mode="install"
launch_after=0
for arg in "$@"; do
    case "$arg" in
        --update) mode="update" ;;
        --launch) launch_after=1 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
user_root="${HERMES_HELPER_USER_ROOT:-$HOME}"
data_home="${XDG_DATA_HOME:-$user_root/.local/share}"
state_home="${XDG_STATE_HOME:-$user_root/.local/state}"
install_root="$data_home/hermes-helper"
bin_root="$user_root/.local/bin"
applications_root="$data_home/applications"
backup_root="$state_home/hermes-helper/backups"
timestamp="$(date +%Y%m%d-%H%M%S)"
backup_path="$backup_root/Hermes-Helper-$timestamp"
failed_path="$backup_root/failed-install-$timestamp"

if [[ "${HERMES_HELPER_SKIP_TK_CHECK:-0}" != "1" ]]; then
    python3 -c 'import tkinter' >/dev/null 2>&1 || {
        printf 'Hermes-Helper requires Python Tkinter. Install it first (on Kali/Debian: sudo apt install python3-tk).\n' >&2
        exit 1
    }
fi

if [[ "${HERMES_HELPER_SKIP_TRAY_CHECK:-0}" != "1" ]]; then
    python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("AyatanaAppIndicator3", "0.1"); from gi.repository import AyatanaAppIndicator3, Gtk' >/dev/null 2>&1 || {
        printf 'Hermes-Helper requires the native system-tray bindings. Install them first (on Kali/Debian: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1).\n' >&2
        exit 1
    }
fi

mkdir -p "$bin_root" "$applications_root" "$backup_root"

if [[ -d "$install_root" ]]; then
    mv "$install_root" "$backup_path"
    printf 'Previous installation backed up to: %s\n' "$backup_path"
fi

restore_previous() {
    status=$?
    if [[ $status -ne 0 ]]; then
        if [[ -d "$install_root" ]]; then
            mv "$install_root" "$failed_path"
        fi
        if [[ -d "$backup_path" ]]; then
            mv "$backup_path" "$install_root"
            printf 'Installation failed; the previous version was restored.\n' >&2
        fi
    fi
    exit "$status"
}
trap restore_previous EXIT

mkdir -p "$install_root"
cp -a "$source_root/." "$install_root/"
if [[ -d "$install_root/.git" ]]; then
    mv "$install_root/.git" "$backup_root/source-git-$timestamp"
fi
find "$install_root" -type d -name __pycache__ -prune -exec rm -r {} + 2>/dev/null || true
find "$install_root" -type f -name '*.pyc' -delete 2>/dev/null || true

launcher="$bin_root/hermes-helper"
printf '%s\n' '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    "exec python3 '$install_root/launcher.py' \"\$@\"" > "$launcher"
chmod 0755 "$launcher"

desktop_file="$applications_root/hermes-helper.desktop"
printf '%s\n' \
    '[Desktop Entry]' \
    'Type=Application' \
    'Name=Hermes-Helper' \
    'Comment=Cloud and local Hermes Agent control center' \
    "Exec=$launcher" \
    "Icon=$install_root/assets/hermes_helper_icon.png" \
    'Terminal=false' \
    'Categories=Utility;System;' \
    'Keywords=Hermes;Ollama;LLM;Telegram;AI;' > "$desktop_file"
chmod 0644 "$desktop_file"

trap - EXIT
printf '\nHermes-Helper installed successfully.\n'
printf 'Launcher: %s\n' "$launcher"
printf 'Application menu: Hermes-Helper\n'
printf 'Automatic startup: OFF (it can be enabled explicitly in Settings)\n'

if [[ "$mode" == "update" && -d "$backup_path" ]]; then
    printf 'Rollback backup: %s\n' "$backup_path"
fi

if [[ $launch_after -eq 1 ]]; then
    nohup "$launcher" >/dev/null 2>&1 &
fi
