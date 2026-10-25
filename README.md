# CPersona × local-search Bridge

**CPersonaのテキスト記憶とlocal-searchの画像・ファイル検索をつなぐ、ローカル専用のMCPアダプター計画。**

> **Status: M0進行中（Bridgeサーバー未実装）**
>
> このリポジトリには現時点で動作するBridgeサーバーやMCPツールはありません。README・設計・チケットは**実装仕様と作業計画**です。利用を開始できる状態であるかのようには扱わないでください。B-01の契約監査（[docs/10](docs/10-contract-audit.md)）と合成fixture＋contractテストが加わりましたが、これらは仕様確定用の検査資産であり、連携サービスは仍然として存在しません。

## 目的と背景

[CPersona](https://github.com/pippirium/CPersona)は会話・意思決定などの記憶を原文のまま保持し、MCP経由でテキストの根拠を返します。[local-search](https://github.com/pippirium/local-search)はEmbeddingGemma 2でテキスト・画像・動画を索引化し、文章や画像からローカルファイルを探します。

Bridgeは両者を置き換えず、**記憶への画像の紐付け、画像参照の検索、明示的な画像取得**だけを担当します。

- 会話の記憶は **CPersona** が管理し、DBや検索方法を変更しない。
- 画像の原本・索引・Embedding推論は **local-search** が管理する。
- どの記憶がどの画像を参照するかは **Bridgeの小さなSQLite** に保存する。
- 通常のrecall/reconstructは画像を自動読込せず、**画像ID・根拠への参照だけ**を返す。
- エージェントが画像を必要と判断して**明示的に要求したときだけ**取得する。

この方式なら、画像のバイナリをCPersonaの記憶DBに入れずに利用できます。M1ではすでにlocal-searchが索引化したファイルをそのまま参照し、M2では必要に応じてBridgeの管理フォルダへコピーして新しい画像を登録する計画です。ユーザーの元ファイルを移動・変更・削除しません。

## 構成

~~~mermaid
flowchart LR
  Agent[Trusted local AI agent] -->|MCP| C[CPersona: textual memories]
  Agent -->|MCP stdio| B[Bridge: metadata and explicit asset fetch]
  B --> S[(Bridge SQLite associations)]
  B -->|localhost HTTP| L[local-search]
  L --> V[(LanceDB + own SQLite)]
  L --> F[Original files: read-only]
  L --> G[EmbeddingGemma 2]
~~~

**M1は同じMac上のローカル利用を前提**とし、Bridgeはstdio MCP、local-searchは既存の127.0.0.1:18765 APIを利用します。local-searchのHost/Originガードを無効化したり、ネットワーク公開する機能は含めません。

## 連携でできるようにすること（計画）

| ユースケース | マイルストーン | 進捗 |
| --- | --- | --- |
| local-searchの既存画像とCPersona記憶を関連付ける | M1 | 未実装 |
| 記憶に関連する画像IDの一覧・検索 | M1 | 未実装 |
| 明示的な要求でのみ画像をMCPへ返す | M1 | 未実装 |
| 同じファイルの重複関連付けと再起動時の永続化 | M1 | 未実装 |
| Agentから明示指定した新しい画像を安全に取り込む | M2 | 未実装 |
| 索引再構築・移動・消失時の参照整合性を回復する | M2 | 未実装 |
| 3サービスの結合テストとLLM不要の画像検索ベンチマーク | M3 | 未実装 |

公開リポジトリのコードに秘密鍵・個人の会話ログ・画像・ローカル絶対パスを含めないでください。既存のlocal-searchは個人用途のプロジェクトであり、本リポジトリはその内部実装を複製せず**既存HTTP API**経由で接続します。

## 主要な設計判断

1. **実装責任の分離:** BridgeはCPersonaやlocal-searchのDBを直接変更しない。
2. **安定したID:** Bridgeのasset_idを永続IDにし、local-searchのfile_idは変化し得る索引参照として保持。再構築後は同一性を再検証する。
3. **明示的な画像取得:** 通常の記憶検索は画像を埋め込まない。画像はget_assetで初めて返す。
4. **スコープ管理:** CPersonaのagent_id/project_id/channelとインスタンス識別子を記録。ただし自己申告IDだけでは本人認証にならないため、M1は**信頼されたローカルユーザーのプロセス限定**。
5. **失敗時は保守的に:** ファイルが移動・消失・再索引されて同一性が確認できない場合、別の画像へ黙って関連付けない。
6. **軽量な実行:** EmbeddingGemma 2をBridgeで再ロードせず、local-search側へ検索を委任する。
7. **オフラインの評価:** Recall/NDCG等の検索評価にはGPT-4.1-mini等の生成LLMを使わない。回答正答率とは指標を分ける。

詳細は[意思決定記録](docs/09-decisions.md)と[アーキテクチャ](docs/02-architecture.md)をご覧ください。

## 詳細設計・作業計画

| ドキュメント | 内容 |
| --- | --- |
| [01 — Requirements](docs/01-requirements.md) | 目的、対象範囲、MVP、機能・非機能要件、完了条件 |
| [02 — Architecture](docs/02-architecture.md) | コンポーネント、実際にあるHTTP API、通信フロー、障害時の動作 |
| [03 — Data model](docs/03-data-model.md) | SQLiteテーブル案、UUID・スコープ・索引ID、マイグレーション |
| [04 — Interfaces](docs/04-interfaces.md) | 提案するMCPツール、request/response、エラー・アクセス制御 |
| [05 — Delivery plan](docs/05-delivery-plan.md) | M0～M3の実行順、依存関係、完了ゲート、PR運用 |
| [06 — Tickets](docs/06-tickets.md) | **18件の実装チケット**と個別受入条件 |
| [07 — Evaluation](docs/07-evaluation.md) | LLM不要の検索精度、評価データ、メトリクス、再現性 |
| [08 — Security & operations](docs/08-security-operations.md) | 脅威、元ファイル保護、バックアップ、障害復旧 |
| [09 — Decisions](docs/09-decisions.md) | 決定事項、提案事項、未解決の設計判断 |
| [10 — Contract audit](docs/10-contract-audit.md) | B-01実測監査: ピンSHA・API実形状・memory_ref・索引世代とfile_id耐久性（合成fixtureとcontractテストが同梱） |
| [11 — ID・スキーマ・scope](docs/11-id-schema-scope.md) | B-02確定事項: 永続asset_id・opaque memory_ref・完全一致scope・凍結DDL（migrations/0001_v1.sql）とlocator状態グラフ |
| [12 — 脅威モデル・メディア方針](docs/12-threat-model-media-policy.md) | B-03確定事項: 信頼境界・T-01〜T-15負例表・取得上限（8MB/40MP/JPEG,PNG,WebP,GIF）・HTTPクライアント規則・ログ秘匿監査 |
| [AGENTS.md](AGENTS.md) | AIエージェントに求める開発・安全ルール |

## 実装ロードマップ

- **M0 — 契約と設計の確定:** CPersonaの実際のmemory ID、local-searchの安定したファイル参照、信頼境界を監査。
- **M1 — 最小MCPアダプター:** 既存画像とのリンク／解除・検索・一覧・明示取得。まずこの段階だけで実用性を確認。
- **M2 — ファイル取り込み・運用:** オプションの管理コピー、索引待ち、破損・切断・再索引への復旧。
- **M3 — 結合評価:** 実サービス同士の統合テスト、画像横断検索のベンチマーク、手順書と安全性レビュー。

GitHub Issueは**Bridgeで全体を統括**し、他リポジトリへの変更が本当に必要なときだけ別途承認を得て作業します。CPersona側のfork policyに従い、[Cloto-dev/CPersona](https://github.com/Cloto-dev/CPersona)へのpushやPRは行いません。

## 想定開発環境（将来）

- Python 3.12、uv、MCP Python SDK、SQLite標準ライブラリ、ローカルHTTPクライアント
- Apple Silicon Mac上に起動済みのlocal-search。モデル推論・LanceDBはlocal-searchが担当
- テストはFake HTTP providerと一時SQLiteを使ってネット接続なしで完結
- 実モデルの速度評価は任意の隔離データセットで明示的に実行

**現在、Bridgeを起動するインストールコマンドはありません。** M1実装後に、正確な環境構築コマンドとMCP登録例を追記します。

## 開発の進め方

[AGENTS.md](AGENTS.md)と[チケット仕様](docs/06-tickets.md)に沿ってIssue単位で実装し、独立した論理変更ごとに詳細なコミットメッセージとCo-authored-by表記を残します。PRにはテスト結果、互換性、リスクとロールバックを記載します。既存のユーザーデータへの書き込みや別リポジトリへのpushは、対象の承認なしには行いません。

## 現在の制約

このリポジトリは現時点で**仕様と実装計画のみ**です。画像取り込み、ファイル検索、MCP連携、モデル比較の実装や動作保証はまだありません。LLM不要の検索品質評価は可能な設計ですが、実測結果を出すまでスコアは未測定として扱います。

## ライセンス

ライセンスは未選定です。公開リポジトリであっても、再配布・改変を許諾するライセンスが付与されているとは限りません。外部へ再利用を案内する前に、別途明示的なライセンス方針を決定します。
