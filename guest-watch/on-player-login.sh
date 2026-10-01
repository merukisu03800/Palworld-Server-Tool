#!/usr/bin/env bash
#
# プレイヤーが接続したときに watch-new-player.sh から呼ばれるフック。
# オーナーかどうか、既にOriginプロファイル適用済みかどうかを判定してログに残し、
# 必要な場合のみ PutServerInit.sh を実行する。
# $1: steam_id (例: steam_765611988xxxxxxxx)
# $2: 表示名

set -uo pipefail

STEAM_ID="$1"
NAME="$2"

OWNER_STEAM_ID="steam_76561198832108289"
TARGET_PROFILE="Origin"

LOG_DIR="/home/ubuntu/SettingTool/log"
LOG_FILE="${LOG_DIR}/player-login.log"
STATE_FILE="/home/ubuntu/SettingTool/.current_profile"

mkdir -p "$LOG_DIR"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [${NAME}/${STEAM_ID}] $1" >> "$LOG_FILE"
}

if [[ "$STEAM_ID" == "$OWNER_STEAM_ID" ]]; then
    log "${STEAM_ID} のため何もしません。"
    exit 0
fi

if [[ -f "$STATE_FILE" ]] && [[ "$(cat "$STATE_FILE")" == "$TARGET_PROFILE" ]]; then
    log "${STEAM_ID} は既に${TARGET_PROFILE}適用済みのため何もしません。"
    exit 0
fi

log "${STEAM_ID} により${TARGET_PROFILE}を適用します。"
/home/ubuntu/SettingTool/PutServerInit.sh "$TARGET_PROFILE"
