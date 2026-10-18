#!/usr/bin/env bash
# Adds MKSCRIPT Studio to the desktop application menu for the current user.
# No administrator rights needed: it only writes to ~/.local/share.
# The launcher points at this repository's ./mkscript-studio script, so after a
# "git pull" the menu automatically opens the new version.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT
readonly APP_ID="io.github.rauldevstack.MkscriptStudio"
readonly DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
readonly LAUNCHER="${ROOT}/mkscript-studio"
readonly DESKTOP_FILE="${DATA_HOME}/applications/${APP_ID}.desktop"

[[ -f "${LAUNCHER}" ]] || { printf '[X] %s was not found\n' "${LAUNCHER}" >&2; exit 1; }
chmod +x "${LAUNCHER}"

mkdir -p -- "${DATA_HOME}/applications"
# The path is quoted in Exec in case the folder contains spaces.
sed "s|^Exec=.*|Exec=\"${LAUNCHER}\" %f|" "${ROOT}/packaging/${APP_ID}.desktop" > "${DESKTOP_FILE}"
chmod 644 "${DESKTOP_FILE}"
install -Dm644 "${ROOT}/src/mkscript_studio/resources/icons/${APP_ID}.svg" \
    "${DATA_HOME}/icons/hicolor/scalable/apps/${APP_ID}.svg"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${DATA_HOME}/applications" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "${DATA_HOME}/icons/hicolor" || true
fi

printf '[✓] Launcher installed at %s\n' "${DESKTOP_FILE}"
