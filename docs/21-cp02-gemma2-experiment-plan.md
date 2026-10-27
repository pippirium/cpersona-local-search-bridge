# CP-02 EmbeddingGemma 2 全LMEB実験計画

Status: 計画済み、未実行。CP-02事前登録条件（[docs/18](18-cp02-comparison-protocol.md)）に沿ったモデル別の実行手順です。この文書は実験実行を開始する指示ではありません。

## 目的

固定revisionのEmbeddingGemma 2を、公開LMEBの全22データセット・193タスク・103コーパスグループでdense検索評価する。既存Jina・BGE-M3結果と同じクエリ、正解qrel、検索候補、デバイス、dtype、長さ、batch、コード、ソフトウェア環境を使い、CP-02の対応比較へ加える。

記録する問い:

1. 103グループをエラーなく完了できるか。
2. Recall@1/5/10、Success@10、MRR@10、nDCG@10のデータセット別・メモリ種別別・等重み平均はどうなるか。
3. M1 Pro/MPS条件で、埋め込み時間、batch-oneクエリ遅延、RSS、MPS driver割当量はどうなるか。

優劣の閾値は事前に設けません。3モデルすべての全件結果が揃うまで勝敗やモデル推奨を出しません。

## 固定条件

| 項目 | Gemma 2の設定 |
| --- | --- |
| モデル | `google/embeddinggemma-2` revision `914f7f89142e33e77833254d9c9b90c3cef7303b` |
| データ | `KaLM-Embedding/LMEB` revision `f9d4294be4a24b8c16d6bfb59d56a6fec4bd95c0` |
| 対象 | 22 datasets / 193 tasks / 103 corpus groups / 159,011 positive-qrel queries |
| 指示ファイル | `task_instructions.json`, SHA-256 `f3f83069f3a3818a89c28de320051521bc37c6b8314d110b8911356eb9adfaa8` |
| 実行環境 | Apple M1 Pro / 32 GB / MPS / bf16。比較対象と同じmacOS、Python package set、platform情報を使う |
| encoder | 最大512 tokens、corpus/query quality batch 8、外側チャンク最大1,024 |
| Gemma設定 | text-only。audio/visionを無効化し、文書`Document`・クエリ`SearchQuery` promptを使う。LMEB task instructionはGemmaの入力へ付けない |
| 資源記録 | process RSSとMPS driver allocationを100 ms間隔でサンプル |
| 遅延標本 | 193タスクごとに最大20クエリを決定的に選び、batch-one embeddingとrankingのp50/p95を記録 |

コーパス本文、正規化、candidate scene、qrelの扱い、集計法はdocs/18のPhase 2条件をそのまま使います。入力本文を別PCへ運ぶ場合もGitには追加しません。

## 実行前ゲート

すべて満たすまでGemma推論を開始しません。

1. CPersona forkのハーネスを、Jina・BGE-M3結果に記録されたソースコミット`bd1c29d737ecdc8b3e02f84364d088c0554b376d`で実行する。checkout後に`git rev-parse HEAD`がこのSHAと一致することを確認する。`full_lmeb.py`と`compare.py`のハーネスfingerprintも既存結果と一致させる。
2. CPersona実験環境の`torch`、`sentence-transformers`、`transformers`、`peft`、`numpy`、`psutil`の各versionと、M1 ProのOS release・architecture・CPU・RAMを含むplatform情報が既存Jina結果と一致することを確認する。比較CLIはソースコミット、packages、condition、platform、harness fingerprintの一致を要求する。異なるPCやOSで条件が一致しない場合、結果を既存Jina/BGEと直接比較しない。全モデルの同一環境再実行か、別条件としての事前登録が必要。
3. LMEBの固定revisionから作った正規化入力103グループが揃い、manifestのdataset fingerprintと一致することを確認する。task instructionファイルも上表のSHAと一致させる。CLIはGemma実行時にもこのファイルを必須引数として検証するが、Gemmaのquery文字列には指示文を付けない。
4. モデルsnapshotを固定revisionで事前取得し、snapshot directoryのbasenameを`914f7f89142e33e77833254d9c9b90c3cef7303b`にする。推論時はlocal-files-onlyで実行する。
5. MPSを使う別workerが動いていないこと、隔離出力先に十分な空き容量があること、結果・チェックポイントの出力先がGit管理対象外であることを確認する。
6. BGE-M3は現在group 8までの部分結果だけである。Gemmaが完了しても、BGE全103グループの結果が揃うまでは3モデル比較を実行しない。

特に2の環境一致が取れない場合は、Gemma単独の参考実測として記録するか、3モデル全体の再実行を行うかを先に決めます。デバイス、batch、dtype、最大長を実行中に変更して既存結果と混ぜません。

## 実行コマンド

CPersona forkのハーネスを含むcheckoutのrootから、固定snapshotと隔離ディレクトリを指定します。`<...>`は各PCのローカルパスへ置き換えます。

```sh
python -m poc.embedding.full_lmeb run --model gemma \
  --model-root <model-snapshot>/914f7f89142e33e77833254d9c9b90c3cef7303b \
  --input-dir <normalized-input-dir> \
  --task-instructions <pinned-task_instructions.json> \
  --checkpoint-dir <gemma-checkpoint-dir> \
  --out <result-dir>/gemma.json.gz
```

ハーネスはMPSへモデルをロードし、bf16・text-only・`Document`/`SearchQuery` promptを適用します。各グループで文書・クエリを2回warmupした後、corpus encode、quality query encode、ranking、batch-one latencyを別々に記録します。長いencodeは外側1,024件以下へ分割し、chunkごとにMPSを同期します。手動でtoken limitやbatchを変えません。

## 停止・再開条件

- 1グループのcheckpointが保存され、完了ログが確認できた境界を再開点にします。再開時はモデルrevision、入力hash、harness hash、ソースcommit、condition、package versionsが一致するcheckpointだけを使います。
- OOM、checkpoint fingerprint不一致、入力検証エラーが出たらその場で停止し、失敗グループとログを記録します。CPU/fp32への切替、batch削減、512-token上限変更は行わず、既存結果と比較可能な同一条件を保ちます。
- 全103グループの完了までは最終`gemma.json.gz`が完成しません。中断時は部分checkpointだけを保存し、全件結果として報告しません。
- 本計画を記録しただけでは推論を再開しません。実行は別途依頼を受けてから行います。

## 完了判定と比較

Gemma runは103 groups、193 tasks、159,011 scored queriesを満たし、エラー0件であることを確認します。報告にはrevision、harness hash、source commit、packages、platform、native dimension、512-token truncation数、埋め込み・ranking時間、RSS/MPS peak、遅延標本数とp50/p95を含めます。資源サンプリングは短いpeakを見逃し得るため、記録値を最大メモリ使用量の保証として扱いません。

BGEが全103グループを完了し、Jina/BGE/Gemmaのquery-level resultが同一条件で揃った後だけ、次の比較を行います。

```sh
python -m poc.embedding.full_lmeb compare \
  --result <result-dir>/jina.json.gz \
  --result <result-dir>/bge.json.gz \
  --result <result-dir>/gemma.json.gz \
  --out <result-dir>/paired.json
```

比較はRecall@1/5/10、Success@10、MRR@10、nDCG@10、タスク内・データセット内・全22データセット等重みのpaired bootstrap（2,000回、seed 1729）、勝敗同点、latencyを報告します。Phase 1 SciFact/smoke、CPersona hybrid検索、LoCoMo最終回答精度、日本語一般品質とは別の結果として扱います。正規化本文、モデルsnapshot、checkpoint、結果JSONはリポジトリへcommitしません。
