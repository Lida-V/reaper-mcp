# REAPER MCP

AIエージェントから、REAPERの公式ReaScript APIを呼ぶ軽量なローカルMCPサーバーです。
トラック、MIDI、FX、ルーティング、プロジェクト保存、レンダーなどをLuaで操作できます。
**Cockos公式MCPではありません。** 独自のMCPアダプターとLuaブリッジです。

Windows向け / Python標準ライブラリのみ / stdio / MIT

## 仕組み

```text
MCPクライアント → Python stdioサーバー → ローカルのファイルキュー
                                           ↓
                               REAPER内のLuaブリッジ
                                           ↓
                               公式ReaScript API → FX・音源
```

REAPERを別の設定ディレクトリで起動します。
通常のREAPER設定やオーディオデバイス設定はコピーしません。
既存のFX索引を再利用し、起動時の全体スキャンを避けます。
索引が未準備なら起動を拒否し、全体スキャンに自動で切り替えません。
ブラウザー操作、画面クリック、プラグインGUIの自動操作は使用しません。

**これは任意Luaを実行する開発者向けツールです。**
設定を分離しても、Luaやロードしたプラグインは実行ユーザーの権限を持ちます。
OSのファイル操作を制限するサンドボックスではありません。
信頼できるMCPクライアントとコードだけで使用してください。

## 必要な環境

- Windows 10/11、64bit版REAPER 7.x。実機検証はREAPER 7.79。
- Python 3.10以降。実機検証はPython 3.13。
- stdio MCPとプロトコル版 `2024-11-05` に対応するクライアント。
- REAPER本体と必要なプラグインは別途インストール・認証してください。
- 同じPCの初期設定済みREAPERリソースフォルダと、既存の `reaper-vstplugins64.ini`。

REAPERはGUIアプリです。別設定での起動を行いますが、完全なヘッドレス動作は保証しません。
評価版の案内、オーディオ設定、明示的にロードしたプラグインの認証などで操作が止まる場合があります。
macOS/Linux、全プラグイン、全MCPクライアントは未検証です。

## 導入

PowerShellで実行します。REAPERのパスは実際の配置に置き換えてください。

```powershell
git clone https://github.com/Lida-V/reaper-mcp.git
cd reaper-mcp
python scripts/configure.py --reaper "C:\Program Files\REAPER (x64)\reaper.exe" --plugin-cache-source "$env:APPDATA\REAPER"
python scripts/reaper_api.py start --show-window --timeout 180
python scripts/reaper_api.py run-lua --file examples/inspect_project.lua
python scripts/reaper_api.py stop
```

`pip install` は不要です。
初回は `--show-window` で専用REAPERを表示し、必要な初回案内やデバイス設定を確認してください。
タイムアウト後もプロセスが動作している場合は、その専用ウィンドウの状態を確認してから `status` を実行します。
`config/local.json` と `runtime/` は自動生成され、Git管理から除外しています。
既存の `config/local.json` は設定スクリプトが上書きしません。
変更する場合は、そのファイルを直接編集してください。

`--plugin-cache-source` は、普段使うREAPERの初期設定済みリソースフォルダです。
標準インストールでは通常 `$env:APPDATA\REAPER` です。ポータブル版は実際のリソースフォルダを指定してください。
コピー対象は `reaper-vstplugins64.ini`、`reaper-vstshells64.ini`、`reaper-vstbridge64.ini` の3ファイルだけです。
プラグイン本体・プリセット・ライセンス・登録情報はコピーしません。
索引はプラグインのインストールや認証を代行せず、ロード成功を保証しません。
索引のコピーはプラグイン本体を実行するスキャンとは異なります。
専用設定では毎回 `vst_scan=1` を適用します。
空の索引からの初回起動ではREAPERがシステムパスを補完してスキャンするため、このサーバーはその起動を拒否します。
準備済みの索引を指定してから再実行してください。登録数はロード成功・認証済み数ではありません。
全体再スキャンは行いません。
ただし、索引に無いプラグインが新しく導入されていると、起動時にREAPERがその分を索引へ追加する場合があります（REAPER 7.81で1件を実測。全件の再スキャンではありません）。
使用するFXは既存索引から識別子で指定するか、対応するFXを絶対パスで明示してロードしてください。
全プラグインが絶対パス指定に対応するとは限りません。検出できない場合も全体スキャンへ自動的に切り替えません。

## MCPクライアントへの登録

CodexやClaude Codeなど、stdioサーバーに対応するクライアントで使います。
設定形式はクライアントごとに異なります。
次の値のPython実行ファイルとスクリプトパスを、自分の環境に合わせて登録してください。

| 項目 | 値の例 |
| --- | --- |
| command | `C:/Python313/python.exe` |
| args | `["-X", "utf8", "C:/tools/reaper-mcp/scripts/reaper_mcp.py"]` |
| transport | `stdio` |

`mcpServers` 形式の設定例は [examples/mcp.json](examples/mcp.json) です。
登録後に `reaper_status` → `reaper_start` → `reaper_run_lua` の順に使います。
MCPサーバーの起動だけではREAPERは起動しません。

## 8つのツール

| ツール | 役割 |
| --- | --- |
| `reaper_status` | 状態（`state`）、トラック数、現在のプロジェクトなどを確認 |
| `reaper_start` | 専用設定のREAPERを起動。応答中なら再起動せず、前のプロセスが残っていれば拒否 |
| `reaper_list_fx` | 登録FXを取得。`query` で名前を絞り込む |
| `reaper_run_lua` | Luaを実行して完了を待つ。`code` と `expected_project` 必須、`timeout_seconds` は1〜600秒 |
| `reaper_submit_lua` | Luaを投入してすぐ `job_id` を返す。長いレンダー向け |
| `reaper_job_result` | `job_id` の結果を取得。`wait_seconds`（0〜600秒）まで待てる |
| `reaper_pin_project` | Python APIから呼ぶスクリプト向けに、対象プロジェクトを固定 |
| `reaper_stop` | 未保存の変更がない場合に専用REAPERへ終了要求 |

`reaper_status` の `state` は次の4つです。

| state | 意味 |
| --- | --- |
| `idle` | 応答中。ジョブなし |
| `busy` | ジョブ実行中。`job_id` と経過秒が付く。実行中はハートビートが止まるのが正常 |
| `stalled` | プロセスはあるが、応答もジョブもない。読み込み中、確認ダイアログ、またはハング |
| `not_started` | 専用REAPERのプロセスがない |

`expected_project` は必須です。対象RPPの絶対パスを渡すと、実行直前に一致を確認します。
空の文字列は未保存の新規プロジェクト、`"*"` は照合なし（プロジェクトを開く処理など）です。
このチェックは実行開始時のみです。Lua実行中のプロジェクト切り替えを制限するものではありません。
結果の `project` に、実行後に開いていたプロジェクトのパスが入ります。
Luaの返り値はJSON化できる値にしてください。空テーブルは `{}` として返ります。

Python APIの `run_lua(code)` は `expected_project` を省略できます。
省略時は `pin_project()` で固定したパスと照合し、固定がなければ照合しません。
再起動直後の専用REAPERは空のプロジェクトを開いているため、編集スクリプトは先に固定してください。

### レンダー

レンダー設定（`RENDER_FILE`、`RENDER_PATTERN` など）をLuaで設定した後、ブリッジの関数を呼びます。

```lua
return REAPER_SUPPORTER_RENDER({overwrite=true})
```

出力先に同名ファイルがあると、REAPERは上書き確認のダイアログを出し、APIが応答しなくなります。
この関数は出力先を先に調べます。`overwrite` を指定しなければエラーをすぐ返し、指定すれば既存ファイルを消してから書き出します。
書き出しの前に、オフラインになったメディアをオンラインへ戻します（`online=false` で省略）。
返り値は出力ファイルとバイト数の一覧です。音が入っているかは別に確認してください。

```json
{
  "code": "return {tracks=reaper.CountTracks(0), tempo=reaper.Master_GetTempo()}",
  "timeout_seconds": 60,
  "expected_project": "C:/music/MySong.rpp"
}
```

FXの挿入や公開パラメーターの変更は `TrackFX_AddByName`、`TrackFX_SetParamNormalized` などを利用します。
GUI専用機能や独自プリセットブラウザーまで操作できるとは限りません。
自動作曲・ミックス・マスタリングの判断はAI側の役割です。このサーバー自体が品質を保証する自動マスタリングエンジンではありません。

## 保存・タイムアウト・終了

- 保存先はLua側で明示します。`projects/` と `output/` はGitから除外していますが、書き込み先を強制する仕組みではありません。
- 未保存の変更があると終了を拒否します。RPPを保存してから `reaper_stop` を使ってください。
- タイムアウトで実行中のLuaやレンダーを強制停止しません。エラーに出る `job_id` を `reaper_job_result` で待ち、同じ処理を送り直さないでください。
- ジョブは1件ずつ、投入した順に実行します。
- REAPERが終了した状態から起動するときは、取り残された未実行リクエストをキャンセル扱いに、実行途中だったものを `orphaned` 扱いにします。再実行はしません。
- 応答が途絶えても記録されたプロセスが残っている場合（`busy`・`stalled`）、重複起動を拒否します。`stalled` はREAPERの画面を確認してください。ダイアログを閉じる機能はありません。
- 完了したジョブのLuaソースと結果は `runtime/queue/done/` へ移り、14日を過ぎたものは次の起動時に削除します。`runtime/` にはローカルパスも残ります。共有しないでください。

## テスト

```powershell
python -m unittest discover -s tests -v
```

このテストはREAPERを起動せず、プロトコル・入力検査・設定分離を確認します。
実機テストは設定済みの専用インスタンスを使用し、合成音のみを生成します。

```powershell
python tests/smoke_reaper.py
```

実機テストは空の未保存プロジェクトに限定し、ReaSynthのロード、MIDI、FXパラメーター変更、WAVレンダー、音声の非無音、RPP保存、プロジェクト一致チェック、既存の出力先への書き出しの拒否、実行中の `busy` 表示と重複起動の拒否、後からの結果取得を検査します。
出力はGit対象外の `output/` に保存します。
詳細は [docs/VALIDATION.md](docs/VALIDATION.md) を参照してください。

## ライセンスと範囲

このリポジトリのコードは [MIT License](LICENSE) です。
REAPER、各プラグイン、MCPクライアントの権利や利用条件は各提供元に従います。
本リポジトリにはREAPER本体、商用プラグイン、音源、制作楽曲、認証情報を含みません。

公式APIの参照先: [REAPER ReaScript](https://www.reaper.fm/sdk/reascript/reascript.php)。
