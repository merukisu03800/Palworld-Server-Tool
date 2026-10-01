#!/usr/bin/env bash
#
# palserver.service の journal を監視し、プレイヤーが "connected the server" した瞬間に
# on-player-login.sh を呼び出す常駐スクリプト。
# オーナー判定・プロファイル適用要否の判定とログ出力は on-player-login.sh 側で行う。
# systemd (palserver-guest-watch.service) から起動される想定。

set -uo pipefail

HOOK_SCRIPT="/home/ubuntu/SettingTool/on-player-login.sh"

journalctl -u palserver -f -n 0 -o cat | while IFS= read -r line; do
    [[ "$line" == *"connected the server."* ]] || continue

    if [[ "$line" =~ User\ id:\ (steam_[0-9]+) ]]; then
        steam_id="${BASH_REMATCH[1]}"
    else
        continue
    fi

    name="$(sed -E 's/^\[[^]]*\] \[LOG\] ([^ ]+) .*/\1/' <<< "$line")"

    "$HOOK_SCRIPT" "$steam_id" "$name" &
done
