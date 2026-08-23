# x-judge — レスバ判定ボット（X版）

スレッド内でボットをメンションすると、そこまでの議論を**論証構造の5軸**で採点し、スコアと勝者を1ツイートでリプライします。

## 構成

| 役割 | 使うもの | 料金 |
|---|---|---|
| 定期トリガー | cron-job.org（外部cronサービス・2分間隔） | 無料 |
| 実行基盤 | GitHub Actions（workflow_dispatch） | 無料（publicリポジトリ） |
| SNS API | X API v2（従量課金） | 読み取り $0.005/件、投稿 $0.015/件 |
| 判定 | Gemini API `gemini-3.6-flash` | 無料枠 |
| 状態保存 | `state/seen.json` をリポジトリにコミット | 無料 |

## 採点軸（各20点・計100点）

| 軸 | 内容 |
|---|---|
| 根拠 | 具体的な根拠・出典・事例を添えているか |
| 論点 | 当初の論点を維持しているか（すり替え・ゴールポスト移動を減点） |
| 応答 | 相手の問いに正面から答えているか（無視・はぐらかしを減点） |
| 一貫 | 自分の発言間に矛盾がないか |
| 誤謬 | 藁人形論法・人身攻撃・レッテル貼りがないか |

思想や立場の是非、事実の真偽は**採点しません**。「根拠を示したか」という形式面のみを見ます。

## 判定結果の形式

1ツイート（280文字以内）に論点・点数表・勝者をまとめて返します。
返信内のハンドルは `@` なしで表示します（敗者への通知・晒し上げ防止のため）。

```
⚖️ レスバ判定
論点: 移民受け入れの是非
user_a 91点
　根拠15/論点19/応答19/一貫19/誤謬19
user_b 64点
　根拠13/論点15/応答12/一貫14/誤謬10
🏆 勝者: user_a
```

## セットアップ

### 1. X Developer Portal でアプリ作成
- [developer.x.com](https://developer.x.com) でプロジェクト・アプリを作成
- 従量課金プランを有効化
- Access Token は**ボットアカウントのもの**を使用（Read and Write 権限）

### 2. Gemini APIキー取得
Google AI Studio でキーを発行（無料枠あり）。

### 3. リポジトリ作成
このディレクトリをGitHubにpush。**publicリポジトリ推奨**。

### 4. Secrets 登録
リポジトリ → Settings → Secrets and variables → Actions → New repository secret

| Secret名 | 値 |
|---|---|
| `X_HANDLE` | ボットのXハンドル（@なし） |
| `X_API_KEY` | API Key (Consumer Key) |
| `X_API_KEY_SECRET` | API Key Secret |
| `X_ACCESS_TOKEN` | Access Token（ボットアカウント） |
| `X_ACCESS_TOKEN_SECRET` | Access Token Secret |
| `X_BEARER_TOKEN` | Bearer Token（URLデコード済みの値） |
| `GEMINI_API_KEY` | Gemini APIキー |

### 5. cron-job.org で定期トリガーを設定
GitHub Actionsのcronは遅延が大きいため、外部サービスで2分ごとに`workflow_dispatch`を呼び出します。

1. [cron-job.org](https://cron-job.org) で無料アカウントを作成
2. 「CREATE CRONJOB」で以下を設定：

| 項目 | 値 |
|---|---|
| URL | `https://api.github.com/repos/<owner>/Project_X/actions/workflows/judge.yml/dispatches` |
| Request method | `POST` |
| Execution schedule | Every 2 minutes |

3. Headersに以下を追加：

| Header名 | 値 |
|---|---|
| `Authorization` | `Bearer <GitHubのPersonalAccessToken>` |
| `Content-Type` | `application/json` |
| `Accept` | `application/vnd.github.v3+json` |

4. Request bodyに入力：
```json
{"ref":"main"}
```

### 6. 動作確認
Actions タブ → x-judge → Run workflow → `dry_run` にチェック。
実際には投稿せず、判定結果がログに出ます。

### ローカル実行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 値を埋める
export $(grep -v '^#' .env | xargs)
python -m src.main
```

## 環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `DRY_RUN` | 空 | `1` で投稿せずログのみ |
| `MAX_PER_RUN` | 3 | 1回の実行で処理する最大メンション数 |
| `MAX_AGE_MINUTES` | 90 | これより古いメンションは無視 |
| `MIN_POSTS` | 4 | これ未満の投稿数なら判定しない |
| `GEMINI_MODEL` | gemini-3.6-flash | 判定モデル |

## Bluesky版との主な違い

| 項目 | Bluesky版 | X版 |
|---|---|---|
| API認証 | アプリパスワード1つ | Bearer Token + OAuth 1.0a（5つのキー） |
| スレッド取得 | 親ノードを遡る | `conversation_id`で一括検索 |
| 文字数上限 | 300文字 | 280文字 |
| ハンドル表示 | `@handle`（facetなし） | `handle`（@なし） |
| API費用 | 無料 | 従量課金（使った分だけ） |
| 状態管理 | URI + 経過時間 | tweet ID + since_id |

## 設計上の判断メモ

- **since_idによる差分取得**：毎回全メンションを取得するのではなく、前回取得した最新IDより新しいメンションだけを取得します。新しいメンションがなければ0件返却→0円です。
- **二重返信防止を2層にしている理由**：第1層として`state/seen.json`で処理済みIDを管理し、第2層としてX上の会話にボットの返信が既にあるかをAPI経由で確認します。
- **判定結果のハンドルに@を付けない理由**：X上で@usernameを書くと相手に通知が届きます。判定の敗者側への一方的な通知・集団攻撃を防ぐため、ハンドルは@なしで表示しています。
- **並列実行制御**：`concurrency: group: x-judge, cancel-in-progress: false`により、推論が2分を超えても処理は継続され、次のトリガーはキュー待機後に起動します。

## 運用上の注意

- プロフィールに「自動判定ボットです」と明記してください。
- X APIの従量課金はメンション取得（$0.005/件）・投稿（$0.015/件）が発生します。`since_id`により新規メンションがない実行は0円です。
- X APIの利用規約により、AIが生成したコンテンツを投稿する場合はその旨を明示する必要があります。
