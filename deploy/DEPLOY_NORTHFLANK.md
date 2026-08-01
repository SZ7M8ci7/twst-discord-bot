# Northflank デプロイ手順

## Koyeb の代替として向いている理由

2026年3月22日時点で、Northflank の公式 pricing ページには無料の `Sandbox` プランとして次の内容が記載されています。

- `Always-on-compute - no sleeping :)`
- `2x free services`

アイドル時にスリープする無料枠より、常時接続が必要な Discord bot にはこちらの方が向いています。

公式情報:

- https://northflank.com/pricing
- https://northflank.com/docs/v1/application/getting-started/build-and-deploy-your-code

## 代替候補の比較

- `Northflank`: このリポジトリをそのまま移しやすいです。Dockerfile と GitHub 連携でデプロイできます。
- `Oracle Cloud Always Free`: 長時間動かす用途では比較的安定しやすいですが、VM の管理が必要です。
- `Render` / `Railway`: 始めやすい反面、常時接続が必要な Discord bot の無料運用にはあまり向いていません。

## このリポジトリで変更した内容

- `deploy/northflank.template.json` を追加
- 旧 Koyeb 用 keep-alive workflow を削除
- ヘルスチェック用サーバーが `PORT` を使うように変更
- `/healthz` エンドポイントを追加
- ビルドコンテキストを小さくするため `.dockerignore` を追加
- ホスティング環境向けに大文字の環境変数名をサポート
- 不正な空同期メッセージを拒否し、家具名を完全一致で照合
- スプレッドシートを正として「済」リアクションを起動時・定期的に同期

## 推奨する環境変数

Northflank の template argument overrides に次を設定してください。

- `DISCORD_TEAM_TOKEN`
- `GOOGLE_PROJECT_ID`
- `GOOGLE_PRIVATE_KEY_ID`
- `GOOGLE_PRIVATE_KEY`
- `GOOGLE_CLIENT_EMAIL`
- `GOOGLE_CLIENT_X509_CERT_URL`

任意:

- `GOOGLE_CLIENT_ID`
- `SYNC_SOURCE_ID`: GAS が投稿に使う Webhook ID。設定すると、その送信元以外の同期命令を拒否します。
- `SHEET_SYNC_INTERVAL_MINUTES`: スプレッドシートとの同期間隔。既定値は `10` 分です。

Discord トークンは後方互換のため、従来の `DISCORD_TOKEN` と `TOKEN` も引き続き利用できます。

## デプロイ手順

1. このリポジトリを GitHub に push します。
2. Northflank で新しい template を作成し、`deploy/northflank.template.json` の内容を import します。
3. template を実行する前に、template settings を開いて上記の argument overrides を設定します。
4. template を実行します。
5. 作成された service が `/healthz` で healthy になることを確認します。
6. 起動ログに `Done-reaction restoration completed` が出ることを確認します。
7. 移行確認後、古い Koyeb の service は停止または削除します。

## 「済」リアクションの復元

サービス起動時にスクショチャンネルを一度索引化し、スプレッドシートの `B:C` を一括取得して照合します。その後は既定で10分ごとにシートを再取得し、`未入力`・`編集中` は「済」を外し、それ以外の状態は「済」を付けます。

同期チャンネルの大量の履歴は遡りません。新規スクショは投稿時に索引へ追加されるため、定期同期で反映されます。デプロイ後の初回起動で遡及同期が自動実行され、個別の復旧コマンドは不要です。

## 秘密鍵に関する注意

`GOOGLE_PRIVATE_KEY` には次のどちらでも設定できます。

- 通常の複数行 private key
- `\n` を含む1行文字列

アプリ側で実行時に `\n` を実際の改行へ変換します。
