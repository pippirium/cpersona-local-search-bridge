# CPersona × local-search Bridge

CPersona の記憶と local-search のローカルファイル検索を、安全に連携させるための独立したアダプターです。

> **Status: planning / not implemented.** 現時点では接続機能・MCPツール・自動取り込みは実装されていません。

## 連携先

- [CPersona fork](https://github.com/pippirium/CPersona) — エージェントのテキスト記憶・検索。
- [local-search](https://github.com/pippirium/local-search) — EmbeddingGemma 2 によるテキスト・画像・動画のローカル横断検索（現時点では個人開発用）。
  
このリポジトリは、両方を直接改変せずに接続する薄い層として計画しています。記憶データや画像原本をコミットしないでください。

## 開発状況

初期化後に要件、設計、段階的実装計画、個別チケットを追加します。機能の実装はそれらのレビュー後に開始します。
