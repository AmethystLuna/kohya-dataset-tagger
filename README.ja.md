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

**kohya 形式の学習セット**のためのスタンドアロンなタガー。LoRA ／ フルファインチューニング ／ DreamBooth はどれも同じ `image_dir` + `.txt` サイドカー形式を使い、一連の作業はすべてブラウザーで完結する：

**ディレクトリ閲覧 → ギャラリープレビュー → 画像ごとのタグ編集 → タグのフィルタリング → WD14 による一括キャプション付け → 学習に使える `dataset.toml` の生成**

これは **Anima-Standalone-Trainer** のコンパニオンツールであり、そのプラグインではない：独自のリポジトリ、独自のプロセス、独自のページを持ち、トレーナーとはファイルシステムの契約（データセットディレクトリ、`dataset.toml`、学習キャッシュ）以外に何も共有しない。

## ステータス

**V0.1 · P0 完了**。バックエンド、フロントエンド、タガー、モデルのダウンロード、`dataset.toml` の生成はすべて実装済みで、P0 の受け入れラウンドに合格した。

```text
pytest test/ -q -n auto -m "not network"   1079 passed, 31 skipped   # クローン直後：コミット済みのサンプルデータセット、タガーモデルなし
pytest test/test_docs_index.py -q          15 passed
```

数値は CI（`windows-latest`、Python 3.10、2026-09-21）で測定した。対象が存在し得ない基準——実際の学習セット、ダウンロード済みのモデル、本物の語彙——は自分自身をスキップしてそう告げるので、クローン直後でもグリーンになる。受け入れテストは独立に書かれたもので、トレーナー自身の `config_util` が採点する。実際のデータセットとトレーナーのチェックアウトの両方が必要なので、ローカルのゲートのままにしてある。CI が何を実行するかは [CONTRIBUTING.md](CONTRIBUTING.md)（英語）にある。

## トレーナーが教えてくれない 2 つのこと

どちらの失敗も無言で起き、どちらも 1 回分の学習を無駄にする。

**① キャプションを書いてもテキストエンコーダーのキャッシュは再構築されない。** トレーナーは古いタグのまま学習を続け、何も報告しない。だからこのツールが保証するのは「編集」ではなくキャッシュだ：`.txt` を書き込むたびに対応する `cache_text_encoder/<name>_anima_te.safetensors` も削除し、削除できないときは握りつぶさずエラーにする。根拠と事後分析：[.github/memory/encoder-cache-invalidation.md](.github/memory/encoder-cache-invalidation.md)（英語）。

**② トレーナーの画像列挙は再帰しない。** `glob_images()` は 1 階層しか走査しないので、`image_dir` が親ディレクトリを指すと学習セットは空になる。実際の学習セットは数百個のサブディレクトリになるため、このツールは**ディレクトリごとに 1 subset** を生成し、トレーナー自身の設定バリデーター（[tools/verify_toml_with_trainer.py](tools/verify_toml_with_trainer.py)）で受け入れテストを受ける。

その過程で、トレーナーが黙って受け入れてしまう 6 種類の問題も報告する：キャプションの欠落、`\,` を含むキャプション（2 つのタグとして読まれる）、`max_bucket_reso` より大きい画像、alpha 付きの画像、画像数が少なすぎる、空のディレクトリ。

## インストールと実行

| やりたいこと | Windows | Linux / macOS |
|---|---|---|
| 初回：環境を作る | `setup_env.bat` | `./setup_env.sh` |
| 毎回：起動してブラウザーを開く | `start.bat` | `./start.sh` |
| 同じ 2 つを中国ミラー経由で | `setup_env_cn.bat` | `./setup_env_cn.sh` |

ページを開く前に、ランチャーは次をすべて行う：

- `.venv` がなければ先に初期化するかどうかを尋ねる；
- 初回に一度だけデータセットのルートディレクトリを尋ね、`roots.txt`（gitignore 対象）に保存する。以後は尋ねない；
- ポートが使用中なら次に移る（3001 → 3002 → …）；
- サービスが実際に応答してからブラウザーを開くので、接続できないページを見せられることはない；
- フォアグラウンドで動き、Ctrl+C で終了する。

どちらのランチャーも `--dry-run` を受け付ける：コマンドラインを表示し、何も起動しない。

ランチャーを使わなくてもよい：

```powershell
.\scripts\start.ps1 -Roots "D:\datasets\my-lora" -Port 3001
.\scripts\start.ps1 -NoBrowser
```

環境スクリプトには専用のオプションがある：

```powershell
.\setup_env.bat -Gpu cuda        # CUDA EP を使う（torch か nvidia ランタイムが必要）
.\setup_env.bat -Gpu cpu
.\setup_env.bat -Index cn        # 中国ミラープロファイル（setup_env_cn.bat と等価）
.\setup_env.bat -DryRun          # 実行される選択肢を表示するだけ。何もインストールしない
.\setup_env.bat -Recreate        # venv を作り直す（絶対パスを表示し、先に確認を求める）
```

依存するのはこのリポジトリ自身の `.venv` だけ（約 250 MB）。**torch は不要**で、トレーナーの venv には何もインストールしないこと。

**中国本土のネットワーク**では `setup_env_cn.*` を使う：実装は同じで、パッケージの取得元だけが違う。`USTC → Aliyun → pypi.org` の順に試し、前が失敗したときだけ次へ進むので、ミラーが一時的に落ちていてもインストールできる。2026-09-19 に 245 MB の `onnxruntime-gpu` を取得したときの実測：USTC 8.4–11 MB/s、Aliyun 0.86–1.25 MB/s、pypi.org 0.55–1.01 MB/s。別のミラーを使うには `KOHYA_TAGGER_PIP_INDEX=https://your/simple` を設定する（複数ならカンマ区切り、順に試す）。

## GPU

速度を決めるのは onnxruntime なので、Windows のセットアップスクリプトは **DirectML** 版を既定でインストールする：50 MB、自己完結、ベンダー非依存で、CUDA も cuDNN も要らない。参考マシンでの実測（RTX 4070 Ti SUPER、画像 16 枚、ウォームアップ後の中央値）：

| プロバイダー | 1 枚あたり | 1653 枚のデータセット |
|---|---|---|
| CPU | 0.721 秒 | 19.9 分 |
| **DirectML**（既定） | **0.192 秒** | **5.3 分** |
| CUDA | 0.168 秒 | 4.6 分 |

CUDA は 14% 速く、代わりに 195 MB と torch 依存が増える。だから Windows の既定ではない。

onnxruntime のインストールでは `--index-url` を固定しなければならない（両方のセットアップスクリプトはすでにそうしている）：グローバルの pip インデックスは固まったように見えるほど遅くなることがある（参考マシンでの実測 0.07 MB/s）。インストール後は実際にどれが使われるかを確認する。`get_available_providers()` は当てにならない：

```powershell
.\.venv\Scripts\python.exe tools\check_provider.py    # 実際のモデルでセッションを構築し、使われるプロバイダーを報告する
```

プロバイダーの読み込みに失敗すると onnxruntime は**黙って CPU にフォールバック**する：推論はそのまま動き、4 倍遅い。

## タガーモデル

選んだモデルがローカルになければ自動でダウンロードする。`KOHYA_TAGGER_HF_ENDPOINT` → `huggingface.co` → `hf-mirror.com`（中国向けミラー。マニフェストが本家と一致することを検証済み）の順に試す。

検索ルートは次の順に使われ、同名モデルは最初に一致したものが勝つ：

1. このリポジトリの `models/`——最初の検索ルートであり、既定のダウンロード先
2. 自分で追加したディレクトリ（`model_paths.txt`、次に `--extra-models` / `KOHYA_TAGGER_EXTRA_MODELS`）
3. `%LOCALAPPDATA%/kohya-dataset-tagger/models`——アプリ自身のダウンロードディレクトリ
4. HuggingFace キャッシュ（最後）

**ディレクトリの追加は UI が一番簡単だ**：タガーパネル →「モデルディレクトリ…」→ 絶対パスを貼り付ける。即座に反映され、再起動は不要で、`model_paths.txt` に書き戻される。同等の方法がほかに 3 つある：

```powershell
# 1. model_paths.txt を編集する（1 行に 1 パス。# 以降はコメント、セミコロン区切りも可）——起動時に読まれる
# 2. 環境変数（追加であり、置き換えではない）：
set KOHYA_TAGGER_EXTRA_MODELS=D:\my-taggers;E:\more
# 3. CLI を直接実行する（ランチャーに -ExtraModels 引数はない）：
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --roots "<dataset>" --extra-models "D:\my-taggers"
```

> `scripts\start.ps1` / `scripts\start.sh` は「ポートを選び、roots.txt を読み、サービスを起動する」だけ。モデルディレクトリは転送しない（§3.7）。コマンドライン引数にしてしまうと、UI で削除した行が次の起動で押し戻される。

`--models` / `KOHYA_TAGGER_MODELS` は別物だ：**検索リスト全体を置き換える**ので、リポジトリの `models/` と自動検出されたすべての場所が落ちる。「ディレクトリをもう 1 つ足す」なら上の追加のほうを使う。

既定のリストには、特定の 1 台にしか存在しないパスは入っていない。webui / ComfyUI のモデルがどこにあっても、そのディレクトリを `model_paths.txt` に書けばよい。

**ダウンロードできなくても行き止まりではない**：同じリポジトリの `model.onnx` と `*.csv` を

```text
models/<model-id>/
```

に置く。このディレクトリには、ファイル名がまさにそのことを言っているプレースホルダーファイルがある。ここは最初の検索ルートであり既定のダウンロード先でもあるので、手で置いたものも自動でダウンロードしたものも同じ場所にある。

## データセットの切り替え／モデルディレクトリの追加：再起動は不要

どちらのパスも以前はコマンドライン引数か設定ファイルでしか設定できず、起動時に一度だけ読み込まれていた。今はどちらも UI で変更でき、即座に反映される：

| やりたいこと | 場所 | 効果 |
|---|---|---|
| データセットディレクトリの切り替え／追加 | 左カラムの「ルートディレクトリ」タイトル横の **＋** | すぐに閲覧できる；`roots.txt` に書き戻される |
| データセットディレクトリの削除 | 各ルートの後ろの「削除」 | 最後の 1 つは削除できない（1 つもなくなると何も開けなくなる） |
| モデル検索ディレクトリの追加 | タガーパネル →「モデルディレクトリ…」 | すぐにモデル一覧に現れる；`model_paths.txt` に書き戻される |
| モデル検索ディレクトリの削除 | 同じダイアログの各項目の「削除」 | 自動検出されたものは今回の実行にのみ有効で、UI にもそう表示される |

ブラウザーはネイティブのディレクトリ選択ダイアログを提供できないので、**絶対パスを貼り付ける**。（この 1 つのコントロールのために「任意のディレクトリを一覧する」エンドポイントを開けると、ホワイトリストが無意味になる。）ディレクトリはすでに存在している必要がある：存在しないルートを設定しても、すべてのリクエストが 403 か 404 になるだけだ。

ファイルに書き込めなくても機能は**そのまま動く**が、UI は「再起動後にこの項目は失われます」と表示し、保存できたふりはしない。

## 開発

まず [AGENTS.md](AGENTS.md)（英語）を読む——`.github/memory/` へ導く常駐インデックスだ。作業手順は [.agents/skills/kohya-dataset-tagger-workflow/SKILL.md](.agents/skills/kohya-dataset-tagger-workflow/SKILL.md)（英語）にある。

```powershell
.\.venv\Scripts\python.exe tools\check_relevant.py          # 内側のループ：変更をカバーするテストだけ
.\.venv\Scripts\python.exe tools\check_relevant.py --full   # 報告前のゲート：全スイート + ドキュメントゲート
.\.venv\Scripts\python.exe -m pytest test/ -q -n auto       # 単体テストの全スイート
.\.venv\Scripts\python.exe -m pytest acceptance/ -q         # 受け入れ基準（実際のデータセットが必要）
```

### テストのデータの出どころ

`local_paths.ini` は、チェックアウトが自分の学習セット・モデル・トレーナーを見つける場所だ。コミット済みのテンプレート `local_paths.ini.example` は `test/fixtures/sample_dataset/` 配下のサンプルデータセットを指すので、スイートはそのまま実行できる。キーの一覧は [CONTRIBUTING.md](CONTRIBUTING.md)（英語）にある。

### 受け入れテスト

`pytest acceptance/` には、仕様に対して独立に書かれた基準が入っている。A23 は実際の利用側が採点する：生成された `dataset.toml` をトレーナー自身の `config_util` に渡し、ディレクトリごとに 1 subset を構築できることを要求する。実際のデータセットとトレーナーのチェックアウトが必要なので、ローカルのゲートだ。

### ドキュメント構成

| パス | 何か |
|---|---|
| `AGENTS.md` | 常駐インデックス（≤ 28 KB、ゲートで強制） |
| `.github/memory/MEMORY.md` | カテゴリ表 |
| `.github/memory/INDEX-<category>.md` | カテゴリごとのトピック一覧 |
| `.github/memory/<topic>.md` | 本体 |
| `.github/memory/p0-spec.md` | P0 の凍結された契約と受け入れ基準 |
| `.agents/skills/<name>/SKILL.md` | 作業手順 |

`test/test_docs_index.py` がこの構成の腐敗を防ぐ：予算、到達可能なリンク、孤立トピックの不在、スキルの準拠、そして 3 つの README の同期。

## コントリビュート

Issue とプルリクエストは英語でも中国語でも歓迎する。[CONTRIBUTING.md](CONTRIBUTING.md)（英語）には、セットアップ、2 段階のゲート、変更が守るべきルール、CI が実行する内容がある。セキュリティの問題は [SECURITY.md](SECURITY.md)（英語）へ。このプロジェクトは [Contributor Covenant](CODE_OF_CONDUCT.md)（英語）に従う。

## ライセンス

[MIT](LICENSE) © 2026 AmethystLuna.
