#!/usr/bin/env bash
# Añade MKSCRIPT Studio al menú del escritorio para el usuario actual.
# No necesita permisos de administrador: solo escribe en ~/.local/share.
# El lanzador apunta al script ./mkscript-studio de este repositorio, así que
# tras un «git pull» el menú abre automáticamente la versión nueva.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT
readonly APP_ID="io.github.rauldevstack.MkscriptStudio"
readonly DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
readonly LAUNCHER="${ROOT}/mkscript-studio"
readonly DESKTOP_FILE="${DATA_HOME}/applications/${APP_ID}.desktop"

[[ -f "${LAUNCHER}" ]] || { printf '[X] No se encontró %s\n' "${LAUNCHER}" >&2; exit 1; }
chmod +x "${LAUNCHER}"

mkdir -p -- "${DATA_HOME}/applications"
# La ruta va entre comillas en Exec por si la carpeta contiene espacios.
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

printf '[✓] Lanzador instalado en %s\n' "${DESKTOP_FILE}"
