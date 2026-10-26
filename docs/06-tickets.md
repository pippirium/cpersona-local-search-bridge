# 06 — Detailed implementation tickets

**Historical ticket specifications.** These sections became GitHub Issues; they describe acceptance scope, not current completion. [README](../README.md) reports implemented/unverified status; [Epic #20](https://github.com/pippirium/cpersona-local-search-bridge/issues/20) links the current issue states and merged PR evidence. B-* work belongs to this bridge repository. LS-01 / CP-01 / CP-02 are **cross-project coordination tickets tracked here**, not authorization to change private local-search or CPersona upstream. Each implementation needs its own PR after review.

Use task IDs as stable references even when GitHub issue numbers differ. Detailed work is constrained by requirements 01, architecture 02, data model 03, interfaces 04 and delivery order 05.

**Issue Definition of Done:** code/contract tests, negative tests, security/no-secret check, documented compatibility and rollback, explicit evidence of checks run, one logical commit per task (additional isolated commits only if needed), Co-authored-by: Codex <codex@openai.com>. Do not merge unreviewed or change another repository without authorization.

## B-01 — 外部API・CPersona識別子の契約監査

- **Milestone:** M0
- **Owner repository:** Bridge
- **Depends on:** なし

**Implementation scope**

local-searchの検索・ファイル詳細・原本・状態APIの要求/応答例を現行コードから抽出。CPersonaのMCPで得られる記憶IDの実際の型とagent/project/channel属性を調査し、互換性表と固定fixtureを作る。Index generation・file identity・検索候補filterの可否も明らかにする。

**Acceptance criteria**

実際のAPI応答を模したMockで契約テストが動く。未公開/未提供のAPIは提供済みと誤記しない。記憶IDはopaque型で扱う。実測したリポジトリSHAを記録。

**Deliverables and verification**

docs/02,04とfixtures/contracts、contractテスト。検証前のprovider実装変更は禁止。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-02 — 永続ID・SQLiteスキーマ・scope確定

- **Milestone:** M0
- **Owner repository:** Bridge
- **Depends on:** B-01

**Implementation scope**

asset_idを永続UUIDに、memory_refを不透明文字列に、local-searchのfile_idを一時locatorとして定義。source_instance_id + agent_id + project_id + channelを完全一致条件とする。Nullable UNIQUE、ID再利用、SHAの限界を整理。

**Acceptance criteria**

テーブル制約・SQLite migratorの仕様、最小の3つのスキーマ例、重複リンク/複数記憶/空scopeのテスト方針を記述。Provider欠損でもリンクが保持される。

**Deliverables and verification**

docs/03、スキーマfixture。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-03 — 脅威モデルとメディア取得ポリシー

- **Milestone:** M0
- **Owner repository:** Bridge
- **Depends on:** B-01,B-02

**Implementation scope**

trusted local stdioの限界を明示し、呼び出し側agent_idが認証でないと記録。ファイル選択禁止、get_assetの明示呼び出し、最大byte/pixel/MIME、redirect遮断、ログ秘匿、ソース保護を決定。

**Acceptance criteria**

異なるscope、ID推測、symlink、超大画像、SSRF/redirect、停止中API、Index再構築の負例を列挙。普通のreconstructで画像が自動取得されない。

**Deliverables and verification**

docs/08、セキュリティ試験表。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-04 — stdio MCPサーバー基盤

- **Milestone:** M1
- **Owner repository:** Bridge
- **Depends on:** B-01,B-03

**Implementation scope**

Python 3.12/uvのパッケージ、MCP tool登録、設定検証、bridge_health、構造化エラーとFake providerを作成。常時HTTP listenを増やさない。

**Acceptance criteria**

stdio MCPクライアントからhealthが呼べる。ローカルDB未作成・設定不正・Provider停止が明確。ネット接続不要の自動テストが通る。

**Deliverables and verification**

src/、pyproject.toml/lock、tests、基本起動手順。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-05 — 対応マップSQLiteとマイグレーション

- **Milestone:** M1
- **Owner repository:** Bridge
- **Depends on:** B-02,B-04

**Implementation scope**

assets/asset_links/provider_locatorsの実スキーマ、user_version、外部キー、原子的upsert、重複排除、バックアップ互換、接続クローズを実装。

**Acceptance criteria**

同一リンク二重追加は1件、複数memoryから同一画像参照可能、別scope非混在、再起動耐久、ロールバック、上位version拒否をテスト。

**Deliverables and verification**

migration SQL、Repository、pytestの一時SQLiteテスト。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-06 — local-search HTTPクライアント

- **Milestone:** M1
- **Owner repository:** Bridge
- **Depends on:** B-01,B-03,B-04

**Implementation scope**

POST /api/search、GET /api/files/{id}、GET /api/files/{id}/content、GET /api/statusの型付きラッパー。URLのloopback検証、timeout、redirect禁止、byte数制限、409処理を実装。

**Acceptance criteria**

Mockレスポンスで200/404/409/500/timeout/不正JSON/不正Content-Lengthを検証。異なるhostへのリダイレクトを拒否。Provider DBの直接書き込みなし。

**Deliverables and verification**

HTTP client、型、fakeテスト、エラー対応表。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-07 — link_asset・unlink_asset MCPツール

- **Milestone:** M1
- **Owner repository:** Bridge
- **Depends on:** B-04,B-05,B-06

**Implementation scope**

記憶参照とlocal-searchのファイルIDを検証しasset_idを発行/再利用。Scope一致とProvider IDの検査。リンク解除は元画像・managed copy・CPersona本文を削除しない。

**Acceptance criteria**

多重呼出で冪等。無効memory_ref、未知file_id、異種ファイル、別scopeで漏洩しない。失敗途中でDBに半端なリンクを残さない。

**Deliverables and verification**

MCP toolとMCP/DB結合テスト。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-08 — search_assets・list_memory_assets

- **Milestone:** M1
- **Owner repository:** Bridge
- **Depends on:** B-06,B-07

**Implementation scope**

画像テキスト検索の候補をProvider APIから受け、Bridge関連済み資産とscopeで交差フィルタ。memory別一覧とページングを実装。内部scoreとcoverageを保持。

**Acceptance criteria**

同じqueryでもscope外の候補は返さない。未関連ヒットが上位を占める場合にはpartial coverageを表示し、完全検索と誤認させない。閾値ゼロや空索引にも対応。

**Deliverables and verification**

検索/ページングfixture、正負テストとレイテンシ記録。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-09 — get_assetの明示画像取得

- **Milestone:** M1
- **Owner repository:** Bridge
- **Depends on:** B-03,B-06,B-07

**Implementation scope**

既存リンクに対する明示的要求でのみ画像を読むMCP tool。Providerを再検証して許可MIMEの画像を上限付きで取得。必要な変換のみ一時処理。

**Acceptance criteria**

呼び出さなければ画像が読み込まれない。サイズ/MIME不適合やリンクなしは拒否。メモリ/ログから本文/画像が漏れない。ソースに変更なし。

**Deliverables and verification**

MCP image response fixture、上限・別scope・staleテスト。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-10 — 画像管理フォルダと安全なimport

- **Milestone:** M2
- **Owner repository:** Bridge
- **Depends on:** B-07,B-09

**Implementation scope**

明示承認された画像を読み取り専用ソースから管理rootへコピー。SHA-256、tmp+fsync+atomic rename、重複排除、容量・画像形式・ディレクトリ制約を設ける。M1の外部参照と区別。

**Acceptance criteria**

原本を改変しない。既存ファイルに上書きしない。並列import/プロセス停止/失敗時にゴミが残らない。絶対パスをresponse/logに出さない。

**Deliverables and verification**

managed assetストア実装、ファイル安全性テスト。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-11 — 索引待ち・状態遷移・リトライ

- **Milestone:** M2
- **Owner repository:** Bridge
- **Depends on:** B-06,B-10

**Implementation scope**

管理rootがlocal-searchで監視されている前提を検証。pending→ready/stale/missing遷移と安全なリトライを実装。Bridge側でlocal-searchのDBを書かない。

**Acceptance criteria**

遅延索引、永続的失敗、再起動、切断、差分再スキャンをテストし、存在しないファイルをreadyとしない。

**Deliverables and verification**

状態機械、契約テスト、統合ログ。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-12 — locator照合・修復・監査

- **Milestone:** M2
- **Owner repository:** Bridge
- **Depends on:** B-05,B-06,B-11,LS-01

**Implementation scope**

Index rebuild/rename/move/離脱時のlocatorsを再検証。判定不能のときはstaleにし、明示操作なしに別ファイルを関連付けない。管理コピーの孤立検出、削除は別承認。

**Acceptance criteria**

provider_file_idが再利用されても誤画像を返さない。DBリンクは保持。drives disconnected→reconnectedで復帰可能。reconcileはdry-runを既定に。

**Deliverables and verification**

再索引・ファイルスワップ・復旧のintegrationテスト。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-13 — 3プロセス結合テストと互換性確認

- **Milestone:** M3
- **Owner repository:** Bridge
- **Depends on:** B-09,B-12,CP-01

**Implementation scope**

CPersona fork、local-search、Bridgeを隔離されたテスト環境で起動。記憶作成→画像リンク→検索→明示取得→unlinkとAPI障害再現を実行。

**Acceptance criteria**

実際の3プロセスを使い、テスト用SQLite以外に書き込まない。普通のreconstructはテキストのまま。各リビジョンと成功・失敗結果を記録。

**Deliverables and verification**

integration suiteと手順書。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-14 — 横断検索ベンチマーク

- **Milestone:** M3
- **Owner repository:** Bridge
- **Depends on:** B-08,B-09,B-13

**Implementation scope**

text→image、image→image、memory→imageの評価fixture、NDCG@10/Recall@10/Success@10/MRR@10/p50/p95、per-query JSONとMarkdown reportを生成。画像入力評価はlocal-search APIを再利用。

**Acceptance criteria**

生成LLM不要。公開可能な合成データだけを使用。条件/モデルrevision/生成方法/誤検索/欠損を追跡し、結果未測定の場合は未測定と書く。

**Deliverables and verification**

評価CLI、schema、実測報告（利用者の環境で明示実行）。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## B-15 — 導入手順・backup・release checklist

- **Milestone:** M3
- **Owner repository:** Bridge
- **Depends on:** B-13,B-14

**Implementation scope**

READMEを計画から実装の現状に更新。依存pin、MCP設定例、保管root、backup/restore、index再構築、問題診断、プライバシー点検を作成。

**Acceptance criteria**

真っさらなMacで文書に従って環境構築できる記録（実施不能なら未検証）。復旧手順と元ファイル不変更を確認。誤った完成宣言なし。

**Deliverables and verification**

運用runbook、リリースチェックリスト、手動smoke結果。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## LS-01 — 安定file locatorと検索範囲の互換性監査

- **Milestone:** M0→M2
- **Owner repository:** local-search (proposed; Bridge tracks)
- **Depends on:** B-01

**Implementation scope**

local-search現行API/Index世代/移動検知を調査。file_id以外の照合手段や検索段階のfolder絞り込みが十分か検証。必要なら小さな読み取り専用API変更を別PRとして設計。

**Acceptance criteria**

まず既存APIで実現できるか示す。実装が不要なら『変更不要』で完了。必要になった場合のみpippirium/local-searchにIssue/PRを個別承認の上作成。loopback・guard・read-only不変。

**Deliverables and verification**

API適合性表、reindex検証fixture、Bridge側連携の前提。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## CP-01 — 記憶とasset参照のユーザー操作導線

- **Milestone:** M2
- **Owner repository:** CPersona fork (proposed; Bridge tracks)
- **Depends on:** B-09,B-12

**Implementation scope**

既存reconstructの記憶IDをBridgeへ渡す方法、Agentから2 MCPを呼ぶフロー、参照を記憶本文に記録する場合の形式を文書化。必要性が証明された場合だけCPersona forkに最小変更。

**Acceptance criteria**

記憶DBのproductionに触らず、通常recallは画像を返さない。upstream PR/push禁止。Bridge+Agentの運用で満たせばCPersona本体変更なしとして完了。

**Deliverables and verification**

fork固有のsetup/contract testと運用サンプル。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

## CP-02 — Jina/BGE-M3/EmbeddingGemma 2公平比較

- **Milestone:** M3 (parallel)
- **Owner repository:** CPersona fork (proposed; Bridge tracks)
- **Depends on:** B-01

**Implementation scope**

既存poc/embeddingのJina対Gemma smokeを出発点に、同じ日本語/LMEBデータ、同じ検索条件、固定モデルrevisionでBGE-M3も比較。Prompt役割、dtype、index再作成を考慮。

**Acceptance criteria**

Recall/NDCG/MRR・速度・利用リソースのper-query記録。LoCoMo最終回答正答率と検索指標を混同しない。モデル未計測値は表に書かない。上流・本番DBは変更しない。

**Deliverables and verification**

CPersona forkの独立評価PR（別途承認）、報告書と再現コマンド。

**Safety gate:** production CPersona data, local-search indexes and user original files remain untouched unless the task expressly allows an isolated managed copy. Any API/schema change must update contract fixtures and tests.

