# CPersona × local-search Bridge

**CPersonaのテキスト記憶とlocal-searchの画像・ファイル検索をつなぐ、ローカル専用のMCPアダプター。**

> **Status: 開発版（0.0.0）。stdio MCP・17ツール実装済み、未リリース。**
>
> M0〜M2の実装とオフライン検証、評価CLI・運用手順があります。実モデルの合成色画像probeは[docs/17](docs/17-real-color-probe.md)に記録しました。一般的な品質評価とfresh Mac導入は未検証です。実CPersonaを含む3プロセス試験は[docs/16](docs/16-three-process-acceptance.md)で合成画像・fake embedderによる成功を記録しています。安全性修正 #39〜#43 と検証条件は[決定記録](docs/09-decisions.md)を参照してください。実装済みはリリース承認・安全性の無条件保証を意味しません。

## 目的と背景

[CPersona](https://github.com/pippirium/CPersona)は会話・意思決定などの記憶を原文のまま保持し、MCP経由でテキストの根拠を返します。[local-search](https://github.com/pippirium/local-search)はEmbeddingGemma 2でテキスト・画像・動画を索引化し、文章や画像からローカルファイルを探します。

Bridgeは両者を置き換えず、**記憶への画像の紐付け、画像参照の検索、明示的な画像取得**だけを担当します。

- 会話の記憶は **CPersona** が管理し、DBや検索方法を変更しない。
- 画像の原本・索引・Embedding推論は **local-search** が管理する。
- どの記憶がどの画像を参照するかは **Bridgeの小さなSQLite** に保存する。
- 通常のrecall/reconstructは画像を自動読込せず、**画像ID・根拠への参照だけ**を返す。
- エージェントが画像を必要と判断して**明示的に要求したときだけ**取得する。

この方式なら、画像のバイナリをCPersonaの記憶DBに入れずに利用できます。M1ではすでにlocal-searchが索引化したファイルをそのまま参照し、M2では必要に応じてBridgeの管理フォルダへコピーして索引済み画像の管理コピーを登録します。任意パスをMCP引数としては受け取らず、未索引画像は明示的なBase64入力またはオペレーター管理のstaging経由でのみ登録します。ユーザーの元ファイルを移動・変更・削除しません。

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

## 現在の機能

| ユースケース | マイルストーン | 進捗 |
| --- | --- | --- |
| local-searchの既存画像とCPersona記憶を関連付ける | M1 | 実装済み・合成テスト |
| 記憶に関連する画像IDの一覧・検索 | M1 | 実装済み・合成テスト |
| 明示的な要求でのみ画像をMCPへ返す | M1 | 実装済み・合成テスト |
| 同じファイルの重複関連付けと再起動時の永続化 | M1 | 実装済み・合成テスト |
| 明示指定した索引済み画像を管理folderへコピーする | M2 | 実装済み・opt-in |
| 索引再構築・移動・消失時の照合・状態監査 | M2 | 実装済み・同一性不明なら取得不可 |
| 結合検査・LLM不要の評価CLI | M3 | 実装済み・実provider監査はfake embedder。3プロセスはdocs/16で検証、実モデルの合成色画像probeはdocs/17。一般品質は未検証 |

公開リポジトリのコードに秘密鍵・個人の会話ログ・画像・ローカル絶対パスを含めないでください。既存のlocal-searchは個人用途のプロジェクトであり、本リポジトリはその内部実装を複製せず**既存HTTP API**経由で接続します。

未索引画像の明示的な入力・発見・記憶リンクと、出典メタデータの取得・更新もサポートします。メタデータはユーザー付与ラベルであり、画像の同一性や承認の証明ではありません（[RUNBOOK](operations/RUNBOOK.md)）。

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
| [13 — LS-01 locator監査](docs/13-ls01-locator-audit.md) | reindex/move/消失/rowid再利用でのfile_id耐久性とsearch folder_id制約の結論（**local-search変更不要**・制限は明記） |
| [15 — B-13 実provider監査ログ](docs/15-b13-live-audit-log.md) | 実local-search(31f31048)で実測した耐久・404・rebuildスワップ・在庫route訂正の証跡（eval/README.md と併読） |
| [20 — IMG-09 3プロセス拡張受入](docs/20-img09-three-process-acceptance.md) | 2系統の画像入力、明示リンク、想起、scope負例・運用復旧手順の合成E2E（実行待ち。#62依存） |
| [16 — 3プロセス受入試験](docs/16-three-process-acceptance.md) | 実CPersona・Bridge stdioプロセスと隔離local-searchの合成画像フロー（fake embedder、品質評価ではない） |
| [17 — 実モデル色画像probe](docs/17-real-color-probe.md) | 固定revisionのEmbeddingGemma 2・合成12クエリの実測と限界、per-query成果物 |
| [19 — 画像入力 API の設計契約](docs/19-image-input-contract.md) | 未索引画像の入力・所有scope・明示リンク・安全な検索（設計のみ） |
| [RUNBOOK](operations/RUNBOOK.md) | インストール・MCP登録・pin表・backup/restore・rebuild対応・診断・プライバシー点検・既知の限界 |
| [RELEASE-CHECKLIST](operations/RELEASE-CHECKLIST.md) | リリースゲート（実測記録・運用リハ・セキュリティ・文書誠実性） |
| [14 — CP-01 ユーザー導線](docs/14-cp01-asset-workflow.md) | 記憶ref→link→表示の運用フロー・本文トレーラ形式 `[asset:<uuid4>]`・合成運用サンプル（**CPersona fork変更なし**で成立） |
| [AGENTS.md](AGENTS.md) | AIエージェントに求める開発・安全ルール |

## 実装ロードマップ

- **M0 — 契約と設計の確定:** ✅ 完了（B-01〜B-03, LS-01。docs/10・docs/13）
- **M1 — 最小MCPアダプター:** ✅ 完了（B-04〜B-09。healthを含む6ツール）
- **M2 — ファイル取り込み・運用:** ✅ 完了（B-10〜B-12, CP-01。管理コピー・pending解決・状態機械・監査。計9ツール）
- **M3 — 結合評価:** 🔄 進行中（B-13監査・B-14 eval CLI・B-15 runbookはmainへマージ済み。3プロセス結合はdocs/16で検証済み。実モデルの小規模probeはdocs/17。CP-02・一般品質評価は未完了）

GitHub Issueは**Bridgeで全体を統括**し、他リポジトリへの変更が本当に必要なときだけ別途承認を得て作業します。CPersona側のfork policyに従い、[Cloto-dev/CPersona](https://github.com/Cloto-dev/CPersona)へのpushやPRは行いません。

## 開発環境と起動

- Python 3.12（uv管理）、MCP Python SDK 2.x、httpx、SQLite標準ライブラリ
- local-search が loopback で起動済みであること（モデル推論・索引はlocal-searchの管轄）
- テストはFake HTTP providerと一時SQLiteでネット接続なしに完結（`uv run pytest`）
- 実providerに対する契約tripwireとスモークはオプトイン（[RUNBOOK](operations/RUNBOOK.md) §6）

インストールとMCP登録:

```sh
uv sync
```

MCPクライアント登録例（stdio）と環境変数表は **[operations/RUNBOOK.md](operations/RUNBOOK.md) §1〜§3**。起動はリポジトリrootから `PYTHONPATH=src uv run python -m bridge.server`。

## 開発の進め方

[AGENTS.md](AGENTS.md)と[チケット仕様](docs/06-tickets.md)に沿ってIssue単位で実装し、独立した論理変更ごとに詳細なコミットメッセージとCo-authored-by表記を残します。PRにはテスト結果、互換性、リスクとロールバックを記載します。既存のユーザーデータへの書き込みや別リポジトリへのpushは、対象の承認なしには行いません。

## 現在の状態と制約

MCPサーバー（stdio・17ツール）・管理コピー・状態機械・監査・eval CLI・運用runbookが実装済みで、オフラインスイートは `uv run pytest` で実行できます。2026-10-26の安全性修正後の実行結果は303 passed / 5 skipped（実provider検査はopt-in）です。未測定として正直に記録しているもの:

- **一般的な実画像に対する検索品質・モデル間比較**（docs/17は実EmbeddingGemmaで合成色画像12クエリを測定した限定probe。数値の転記禁止はdocs/07・[eval/README.md](eval/README.md)）
- **工場出荷状態のMacでのインストール手順実演**（RUNBOOK §1に[fresh-Mac unverified]表記）
- **CP-02の独立モデル比較**（#19。別リポジトリ作業の個別承認が必要）
- 自動スケジューラは存在しない（resolve/auditは明示実行のみ=docs/03 §7の設計）

信頼境界: ローカルオペレーター＋stdioトランスポート。`agent_id`等のスコープ引数は**認証ではない**（docs/03 §6）。local-searchのファイル消失は「取得不可」であって別ファイルを返す許可ではない（AGENTS.md不変条件）。

## ライセンス

ライセンスは未選定です。公開リポジトリであっても、再配布・改変を許諾するライセンスが付与されているとは限りません。外部へ再利用を案内する前に、別途明示的なライセンス方針を決定します。
