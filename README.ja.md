<h1 align="center">Kohya Dataset Tagger</h1>

<p align="center">
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.md">English</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/AmethystLuna/kohya-dataset-tagger/actions/workflows/ci.yml"><img src="https://github.com/AmethystLuna/kohya-dataset-tagger/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
</p>

**kohya 形式の学習セット**のためのスタンドアロンなデータセットタガー — LoRA ／ フルファインチューニング ／ DreamBooth はどれも同じ `image_dir` + `.txt` サイドカー形式を使う。一連の作業はすべてブラウザーで完結する：

**ディレクトリ閲覧 → ギャラリープレビュー → 画像ごとのタグ編集 → タグのフィルタリング → WD14 による一括キャプション付け → 学習に使える dataset.toml の生成**

これは **Anima-Standalone-Trainer**（姉妹プロジェクト。ここでは公開していない）の**コンパニオンツール**であり、そのプラグインではない：
独自のリポジトリ、独自のプロセス、独自のページを持ち、トレーナーとはファイルシステムの契約（データセットディレクトリ、`dataset.toml`、学習キャッシュ）を通じてのみやり取りする。

## ステータス

**V0.1 · P0 完了**。バックエンド、フロントエンド、タガー、モデルのダウンロード、dataset.toml の生成はすべて実装済みで、P0 の受け入れラウンドに合格した。

    pytest test/ -q -n auto -m "not network"   1072 passed, 31 skipped   # クローン直後：コミット済みのサンプルデータセット、タガーモデルなし
    pytest test/test_docs_index.py -q          9 passed

テストが必要とするパスはすべて `local_paths.ini`（gitignore 対象。テンプレートはコミット済み）から来る。対象が存在し得ない基準 —— 実際の学習セット、ダウンロード済みのタガーモデル、本物の語彙 —— は**自分自身をスキップし、そう告げる**。だからクローン直後でもグリーンになる。受け入れテストのスイートは独立したもので、トレーナー自身の `config_util` で採点される。公開された数値ではなく、ローカルのゲートのままにしてある。詳しくは [CONTRIBUTING.md](CONTRIBUTING.md)（英語）を参照。

## トレーナーが教えてくれない、このツールが知っている 2 つのこと

この 2 つこそがこのツールの存在理由であり、「設定を手書きする」こととの違いでもある：

**① キャプションを変更しても、トレーナーはテキストエンコーダーのキャッシュを再構築しない。**

エラーも一切出さずに、**黙って古いタグのまま学習を続ける**。
だからこのツールの一番の機能は「編集」ではなく、**編集の直後にキャッシュを正しい状態にすること**だ —— `.txt` を書き込むたびに対応する `cache_text_encoder/<name>_anima_te.safetensors` を削除し、削除できないときは**握りつぶさず**エラーにする。
根拠と事後分析：[.github/memory/encoder-cache-invalidation.md](.github/memory/encoder-cache-invalidation.md)（英語）

**② トレーナーの画像列挙は再帰しない。**

`glob_images()` は 1 階層しか走査しないので、`image_dir` が親ディレクトリを指すと**学習セットは空になる**。
ここでの学習セットは数百個のサブディレクトリになるので、**ディレクトリごとに 1 subset** でなければならない。
ジェネレーターはまさにそれを作り、**トレーナー自身の設定バリデーター**（[tools/verify_toml_with_trainer.py](tools/verify_toml_with_trainer.py)）による受け入れテストに合格する。

その過程で、トレーナーが黙って受け入れてしまう 6 種類の問題を報告する：キャプションの欠落、`\,` を含むキャプション（2 つのタグとして読み取られる）、`max_bucket_reso` より大きい画像、alpha 付きの画像、画像数が少なすぎる、空のディレクトリ。

## クイックスタート

**コマンドは 2 つ。** 最初の 1 回：

```cmd
setup_env.bat
```

以降は毎回：

```cmd
start.bat
```

`start.bat` はブラウザーを開く前に、これだけのことを自分でこなす：

- `.venv` がなければ、先に初期化するかどうかを尋ねる
- 初回に一度だけ**データセットのルートディレクトリ**を尋ね、`roots.txt`（gitignore 対象）に保存する。以後は尋ねない
- ポートが使用中なら自動で次に移る（3001 → 3002 → …）
- **サービスが実際に準備完了してからブラウザーを開く**ので、接続できないページを見せられることはない
- サービスはフォアグラウンドで動く。Ctrl+C で終了する

ランチャーを使わなくてもよい：

```powershell
.\scripts\start.ps1 -Roots "D:\datasets\my-lora" -Port 3001
.\scripts\start.ps1 -NoBrowser
```

Linux / macOS は**同じことをする 2 つのスクリプトで、ルートに左右対称に置いてある**：

```bash
./setup_env.sh       # 冪等。nvidia-smi があれば onnxruntime の CUDA 版、なければ CPU 版をインストールする
./start.sh           # 同じ roots.txt を読み、空いているポートを選び、サービス準備完了後にブラウザーを開く
```

どちらも `--dry-run` を受け付ける：実行されるコマンドラインを表示し、何も起動せずに終了する。

**中国本土のネットワーク**では中国ミラープロファイルを使う（実装は同じで、パッケージの取得元だけが違う）：

```cmd
setup_env_cn.bat
```

```bash
./setup_env_cn.sh
```

`USTC → Aliyun → pypi.org` の順にフォールバックし（前が失敗したときだけ次へ進む）ので、ミラーが一時的に落ちていてもインストールできる。
実測（2026-09-19、245 MB の onnxruntime-gpu）：USTC **8.4–11 MB/s**、Aliyun 0.86–1.25 MB/s、pypi.org 0.55–1.01 MB/s。別のミラーを使うには `KOHYA_TAGGER_PIP_INDEX=https://your/simple` を設定する（複数ならカンマ区切り、順にフォールバック）。

**依存するのは専用の venv だけ**（約 250 MB）で、**torch は不要**。トレーナーの venv には何もインストールしないこと。

`setup_env.bat` のその他の使い方（`scripts\setup_env.ps1` と等価）：

```powershell
.\setup_env.bat -Gpu cuda        # CUDA EP を使う（torch か nvidia ランタイムが必要）
.\setup_env.bat -Gpu cpu
.\setup_env.bat -Index cn        # 中国ミラープロファイル（setup_env_cn.bat と等価）
.\setup_env.bat -DryRun          # 実行される選択肢を表示するだけ。何もインストールしない
.\setup_env.bat -Recreate        # venv を作り直す（絶対パスを表示し、先に確認を求める）
```

## GPU

このマシンでの実測（RTX 4070 Ti SUPER、実画像 16 枚、ウォームアップ後の中央値）：

| プロバイダー | 中央値 | 1653 枚 |
|---|---|---|
| CPU | 0.721 s/image | 19.9 分 |
| **DirectML**（既定） | **0.192 s/image** | **5.3 分** |
| CUDA | 0.168 s/image | 4.6 分 |

Windows では DirectML が既定だ：50 MB、自己完結、ベンダー非依存で、CUDA も cuDNN も要らない。
CUDA は 14% 速いが、195 MB 大きく、さらに torch への依存が付く。
**onnxruntime のインストールでは `--index-url` を明示的に固定しなければならない**（両方のセットアップスクリプトはすでにそうしている）：
グローバルの pip インデックスにフォールバックすると、「固まったように見える」ほど遅くなることがある（このマシンでの実測 0.07 MB/s）。通常プロファイルは `pypi.org` を、中国プロファイル（`setup_env_cn.*`）は USTC → Aliyun → pypi.org を順にフォールバックする。

インストール後のセルフチェックには常にこれを使い、`get_available_providers()` を信用しては**ならない** —— プロバイダーの読み込みに失敗すると onnxruntime は**黙って CPU にフォールバック**し、推論はそのまま動く。ただし 4 倍遅い：

```powershell
.\.venv\Scripts\python.exe tools\check_provider.py    # 実際のモデルでセッションを構築し、**実際に**使われるプロバイダーを報告する
```

## モデルのダウンロード

選択したときにタガーモデルがローカルになければ、自動的にダウンロードされる。エンドポイントの順序は
`KOHYA_TAGGER_HF_ENDPOINT` → `huggingface.co` → `hf-mirror.com`（中国向けミラー。マニフェストが本家と一言一句一致することを検証済み）。

**一番簡単なのは UI で変更する方法**（次のセクションを参照）：「タガー」タブ →「モデルディレクトリ…」で、追加は**即座に反映**され、再起動は不要で、`model_paths.txt` に書き込まれる。他に 3 つの同等な方法がある：

```powershell
# 1. model_paths.txt に書く（推奨。1 行に 1 つ、# 以降はコメント、セミコロン区切りも可）—— サーバーが起動時に読む
# 2. 環境変数（追加であり、置き換えではない）：
set KOHYA_TAGGER_EXTRA_MODELS=D:\my-taggers;E:\more
# 3. CLI を直接実行する（ランチャーに -ExtraModels 引数は**ない**。古いドキュメントをコピーしないこと）：
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --roots "<dataset>" --extra-models "D:\my-taggers"
```

> `scripts\start.ps1` / `scripts\start.sh` は「ポートを選び、roots.txt を読み、サービスを起動する」だけ。
> モデル検索ディレクトリは**ランチャーから転送されない**（§3.7）—— コマンドライン引数にしてしまうと、UI で削除した行が次の起動で押し戻されてしまう。

`--models` / `KOHYA_TAGGER_MODELS` との違いに注意：**あちらは検索リスト全体を置き換える**（設定するとリポジトリの `models/` と自動検出されたすべての場所が落ちる）のに対し、**こちらは追加する** —— 「ディレクトリをもう 1 つ足す」なら後者だ。

**検索ルートの順序がそのまま優先度**だ：リポジトリの `models/` → 自分で追加したもの（`model_paths.txt`）→ ダウンロードしたもの → HF キャッシュ。
明示指定は常に自動検出より前に来る。
既定のリストには、特定の 1 台にしか存在しないパスは**一切**含まれない —— webui / ComfyUI のモデルがどこにあっても、`model_paths.txt` に書けばよい。

**ダウンロードがうまくいかなくても問題ない**：同じディレクトリにある `model.onnx` と `*.csv` をリポジトリの

    models/<model-id>/

に置けばよい（このディレクトリには、ファイル名がまさにこの文であるプレースホルダーファイルがある）。ここは**既定の検索ルートの位置 0**であり、**ダウンロード先**でもある —— 手で置いたものも自動でダウンロードしたものも同じ場所にあり、2 つ目のコピーは作らない。

## データセットの切り替え／モデルディレクトリの追加：再起動は不要

どちらのパスも以前はコマンドライン引数か設定ファイルでしか設定できず、**起動時に一度だけ読み込まれていた**。今はどちらも UI で変更でき、即座に反映される：

| やりたいこと | 場所 | 効果 |
|---|---|---|
| データセットディレクトリの切り替え／追加 | 左カラムの「ルートディレクトリ」タイトル横の **+** | すぐに閲覧可能になる。`roots.txt` に書き戻される |
| データセットディレクトリの削除 | 各ルートの後ろの **×** | 最後の 1 つは削除できない（1 つもなくなると何も開けなくなる） |
| モデル検索ディレクトリの追加 | 「タガー」タブ →「モデルディレクトリ…」 | すぐにモデルのドロップダウンに現れる。`model_paths.txt` に書き戻される |
| モデル検索ディレクトリの削除 | 同じダイアログの各項目の「削除」 | 自動検出されたものは今回の実行にのみ適用される。UI にもそう表示される |

ブラウザーはネイティブのディレクトリ選択ダイアログを提供できないので、**絶対パスを貼り付ける**（この 1 つのコントロールのために「任意のディレクトリを一覧する」エンドポイントを開けると、ホワイトリストが無意味になる）。ディレクトリはすでに存在している必要がある —— 存在しないルートを設定しても、すべてのリクエストが 403/404 になるだけだ。

ファイルの書き込みに失敗しても機能は**そのまま動く**が、UI は成功したふりをせず、「この項目は再起動後に失われます」とはっきり表示する。

## 受け入れテスト

`pytest acceptance/` には、実装者ではなく**仕様に対して独立に書かれた**基準が入っている。その中の A23 は**実際の利用側**が採点する：生成された `dataset.toml` をトレーナー自身の `config_util` に渡し、ディレクトリごとに 1 subset を構築できることを要求する。

## 開発

まず [AGENTS.md](AGENTS.md)（英語）を読む —— `.github/memory/` 配下の第 2 階層のインデックスを指す常駐インデックスだ。
作業手順は [.agents/skills/kohya-dataset-tagger-workflow/SKILL.md](.agents/skills/kohya-dataset-tagger-workflow/SKILL.md)（英語）にある。

```powershell
.\.venv\Scripts\python.exe tools\check_relevant.py          # 内側のループ：変更をカバーするテストだけ
.\.venv\Scripts\python.exe tools\check_relevant.py --full   # 報告前のゲート：全スイート + ドキュメントゲート
.\.venv\Scripts\python.exe -m pytest test/ -q -n auto       # 単体テストの全スイートをマルチコアで
.\.venv\Scripts\python.exe -m pytest acceptance/ -q         # 受け入れ基準（実際のデータセットが必要）
.\.venv\Scripts\python.exe tools\e2e_smoke.py               # 実際の HTTP でエンドツーエンド（サービスが起動している必要がある）
.\.venv\Scripts\python.exe tools\dataset_manifest.py --root "<dataset>" --out snapshot.json   # 自分用のフィンガープリントを保存する
```

`local_paths.ini` は、チェックアウトが自分の学習セット・モデル・トレーナーを見つける場所だ。コミット済みのテンプレートは `test/fixtures/sample_dataset/` 配下のサンプルデータセットを指すので、スイートはそのまま実行できる。
[CONTRIBUTING.md](CONTRIBUTING.md)（英語）にキーの説明と CI ジョブが実行する内容がある：[.github/workflows/ci.yml](.github/workflows/ci.yml) は Linux と Windows をカバーし、huggingface.co と通信する基準は週次のスケジュール実行に回している。

## ドキュメント構成

    AGENTS.md                          常駐インデックス（≤ 28 KB、ゲートで強制）
    .github/memory/MEMORY.md           カテゴリ表
    .github/memory/INDEX-<category>.md トピック一覧
    .github/memory/<topic>.md          本体
    .github/memory/p0-spec.md          **P0 の凍結された契約と受け入れ基準**（コードを変更する前に必ず読む）
    .agents/skills/<name>/SKILL.md     作業手順

`test/test_docs_index.py` がこの構成の腐敗を防いでいる：予算、到達可能なリンク、孤立トピックの不在、スキルの準拠。

## コントリビュート

Issue とプルリクエストは英語でも中国語でも歓迎する。[CONTRIBUTING.md](CONTRIBUTING.md)（英語）には、セットアップ、2 段階のゲート、変更が守るべきルール、CI が実行する内容がある。セキュリティの問題は [SECURITY.md](SECURITY.md)（英語）へ。このプロジェクトは [Contributor Covenant](CODE_OF_CONDUCT.md)（英語）に従う。

## ライセンス

[MIT](LICENSE) © 2026 AmethystLuna.
