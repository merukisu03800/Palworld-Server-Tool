# Palworld Server Tool

AWS EC2上で動かしているPalworld専用サーバーの設定管理・運用を楽にするためのツール群です。
サーバー設定（`PalWorldSettings.ini`）をTSVで一元管理し、ブラウザからサーバーの起動/停止や設定プロファイルの切り替えができます。また、オーナー以外のプレイヤーが接続したときに自動で設定を切り替える仕組みも含みます。

## 構成要素

| パス | 役割 |
|---|---|
| `Palworld-Server-Settings.example.tsv` | サーバー設定のテンプレート（複数プロファイル分を列で管理） |
| `PutServerInit` | TSVの指定プロファイル列から`PalWorldSettings.ini`を生成し、EC2上に配置してpalserverを再起動するスクリプト |
| `ec2-controller-ui-admin.example.html` | ブラウザからEC2の起動/停止・設定プロファイル適用を行う管理画面 |
| `lambda/lmd-ec2-controller/lambda_function.py` | API Gatewayから呼ばれ、EC2操作・SSM経由でのコマンド実行を行うLambda |
| `guest-watch/` | palserverのjournalを監視し、オーナー以外のログインを検知して自動でプロファイルを切り替える常駐ツール |

## 全体のアーキテクチャ

```
ブラウザ (ec2-controller-ui-admin.html)
    ↓ HTTPS POST
API Gateway (REST API, 認証なし)
    ↓
Lambda (lmd-ec2-controller)
    ↓ boto3
EC2 (start/stop) / SSM Send-Command (AWS-RunShellScript)
    ↓
EC2インスタンス上で PutServerInit.sh <Profile> を実行
    → PalWorldSettings.ini を生成・配置
    → systemctl restart palserver
```

もう一系統として、EC2上で常駐する`palserver-guest-watch.service`が`journalctl -u palserver -f`を監視し、オーナー以外のSteam IDの接続を検知すると自動で`PutServerInit.sh Origin`を実行します。

## 1. サーバー設定 (TSV) と PutServerInit

`Palworld-Server-Settings.tsv`（実ファイルはgitignore対象。`Palworld-Server-Settings.example.tsv`をコピーして使う）は以下の列構成です。

- 1列目: 設定内容（説明、空欄の行あり）
- 2列目: 設定値（`PalWorldSettings.ini`のキー名）
- 3〜7列目: `Brown` / `Drop` / `Oillig` / `Power` / `Origin` の5プロファイルごとの値

EC2上で以下を実行すると、指定プロファイルの列から`[/Script/Pal.PalGameWorldSettings]`セクションの`OptionSettings=(...)`形式のiniを生成し、`PalWorldSettings.ini`に配置してpalserverを再起動します。

```bash
./PutServerInit.sh Drop
```

- TSVセル内の値に全角数字が含まれる場合は検出して半角に自動変換します（警告ログ付き）
- 実行ログは`SettingTool/log/PutServerInit.log`に記録されます
- 最後に適用したプロファイル名は`SettingTool/.current_profile`に記録されます（`guest-watch`がスキップ判定に使用）

EC2上の配置パス: `/home/ubuntu/SettingTool/`

## 2. ブラウザからの操作 (EC2 Controller)

`ec2-controller-ui-admin.html`をブラウザで開くと、以下が行えます。

- サーバ起動 / 停止 / 起動状態確認
- プロセス再起動のみ（設定変更なし、`systemctl restart palserver`相当）
- Brown/Drop/Oillig/Power/Originプロファイルの適用（SSM経由で`PutServerInit.sh`を実行）

Lambda (`lmd-ec2-controller`) が受け付けるアクション:

| action | 内容 |
|---|---|
| `status` | EC2の起動状態とパブリックIPを返す |
| `start` | EC2を起動する |
| `stop` | EC2を停止する |
| `restart-server` | `systemctl restart palserver`のみ実行（非同期） |
| `apply-profile` | 指定プロファイルで`PutServerInit.sh`を実行（非同期、`command_id`を返す） |
| `profile-status` | `apply-profile`/`restart-server`で発行された`command_id`の実行結果を取得（ポーリング用） |

**設計上の注意**: API Gateway (REST API) のLambda統合タイムアウトは29秒固定のため、`apply-profile`/`restart-server`はSSMコマンドを投げてすぐ`command_id`を返す非同期方式にしています。ブラウザ側は`profile-status`を2秒間隔でポーリングして結果を表示します。

## 3. ゲストログイン検知による自動プロファイル切り替え (guest-watch)

`guest-watch/`配下の2つのスクリプトをEC2上の`/home/ubuntu/SettingTool/`に配置し、systemdサービスとして常駐させています。

- `watch-new-player.sh`: `journalctl -u palserver -f`を監視し、「connected the server」のログ行からSteam IDと名前を抽出して`on-player-login.sh`を呼び出す
- `on-player-login.sh`: 呼ばれるたびに以下を判定してログに記録し、必要な場合のみ`PutServerInit.sh Origin`を実行する
  - オーナー自身の接続 → 何もしない
  - オーナー以外 かつ 既にOrigin適用済み → 何もしない（無駄な再起動を避ける）
  - オーナー以外 かつ 未適用 → `Origin`プロファイルを適用して再起動

ログ出力先: `/home/ubuntu/SettingTool/log/player-login.log`

systemdユニット定義は`guest-watch/palserver-guest-watch.service`。`palserver.service`に追従して動作する（`After=`/`Requires=`）。

### デプロイ方法 (EC2上)

```bash
# スクリプト本体を配置
cp PutServerInit /home/ubuntu/SettingTool/PutServerInit.sh
cp guest-watch/watch-new-player.sh guest-watch/on-player-login.sh /home/ubuntu/SettingTool/
chmod +x /home/ubuntu/SettingTool/PutServerInit.sh /home/ubuntu/SettingTool/watch-new-player.sh /home/ubuntu/SettingTool/on-player-login.sh

# TSVを配置（実データはgitignore対象、別途用意）
cp Palworld-Server-Settings.tsv /home/ubuntu/SettingTool/

# systemdサービスを登録
sudo cp guest-watch/palserver-guest-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now palserver-guest-watch.service
```

## セキュリティ上の注意

- **このリポジトリはPublicです。** 実際の認証情報・接続先URLを含むファイルは`.gitignore`で除外しています（`Palworld-Server-Settings.tsv`本体、`ec2-controller-ui-admin.html`本体）。リポジトリには`xx`でマスクした`.example`版のみコミットしています。運用に使う実ファイルはローカル環境にのみ保持してください。
- **API Gatewayのエンドポイントには認証がありません。** URLを知っていれば誰でもサーバーの起動/停止/設定変更を実行できる状態です。将来的にはAPIキーなどの認証追加を検討してください。
- `guest-watch/`内のオーナーSteam ID (`OWNER_STEAM_ID`) はハードコードされています。他人と共有する場合は書き換えてください。

## AWS側の構成（参考）

- EC2インスタンス: `Palworld-Server` (ap-northeast-1)
- Lambda: `lmd-ec2-controller` (Python 3.13、IAMロール `role-ec2-controller`)
  - `AmazonEC2FullAccess` + 対象インスタンス・`AWS-RunShellScript`ドキュメント限定のSSM実行権限（インラインポリシー`PalworldSSMRunCommand`）
- API Gateway: `EC2Controller` (REST API, リソース `/ec2-controll`, ステージ `stage`)
- デプロイ(Lambdaコード更新)はAWS CLI (`aws lambda update-function-code`) で行う。`zip`コマンドが無い環境ではPythonの`zipfile`モジュールで代用可能
