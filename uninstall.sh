#!/usr/bin/env bash
set -euo pipefail

user_root="${HERMES_HELPER_USER_ROOT:-$HOME}"
data_home="${XDG_DATA_HOME:-$user_root/.local/share}"
config_home="${XDG_CONFIG_HOME:-$user_root/.config}"
install_root="$data_home/hermes-helper"
launcher="$user_root/.local/bin/hermes-helper"
desktop_file="$data_home/applications/hermes-helper.desktop"
autostart_file="$config_home/autostart/hermes-helper.desktop"
trash_root="$data_home/Trash/files"
timestamp="$(date +%Y%m%d-%H%M%S)"
trash_path="$trash_root/Hermes-Helper-uninstalled-$timestamp"

mkdir -p "$trash_root"
if [[ -d "$install_root" ]]; then
    mv "$install_root" "$trash_path"
fi
for item in "$launcher" "$desktop_file" "$autostart_file"; do
    if [[ -e "$item" ]]; then
        mv "$item" "$trash_root/$(basename "$item").$timestamp"
    fi
done

printf 'Hermes-Helper was removed. Recoverable files were moved to:\n%s\n' "$trash_root"
printf 'Your settings, diagnostic reports, logs, and update backups were preserved.\n'
