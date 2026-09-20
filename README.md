# Jev Video Context Fields — P1

ローカル動画の解析根拠、人物・場所・会話の場、Jevの評価、人間の判定を照合するViewerです。現在の確認用起動は非LIVE debug/replayです。

```powershell
Set-Location -LiteralPath 'C:\dev\jev-video-context-field-po'
.\.venv\Scripts\python.exe -X utf8 -m context_fields.debug_server --port 8876
```

[Viewer](http://127.0.0.1:8876/) の「記録を開く」で保存runを選びます。左動画、右上Jev評価、右下実行ログを表示し、行から原文・参照フレーム・人間判定へ戻れます。実データは同梱していません。既存8876へ二重起動しないでください。

- [P1の共有用要約・残課題](docs/P1_PUBLIC_SUMMARY_JA.md)
- [移行記録](docs/MIGRATION_PUBLIC_JA.md)
- [日本語設計v0.3の参照用写し](docs/JEV_VIDEO_CONTEXT_FIELDS_DESIGN_v0_3_JA.md)
- [CD表示限定契約](docs/SCORE_CD_DISPLAY_ONLY_CONTRACT_v1_JA.md)
- [人間確認Viewer仕様](docs/HUMAN_DEBUG_VIEWER_ADDENDUM_v0_3_H0_JA.md)

Python 3.12と固定依存版は `requirements-local.lock.txt` を参照。GPU packageは `torch==2.9.1+cu128` / `torchvision==0.24.1+cu128`。モデル取得や実Jev送信を通常試験のために起動しません。新しいcloneで私有runに依存する回帰は資料不足となります。合成動画は `scripts/make_demo.py` で生成できます。

実.env・認証情報・元動画音声・実ASR原文・要求応答・実run・人間レビューDB・予算・モデル・仮想環境・cache・backupはGit対象外です。ローカルの過去資料を消毒・上書きして公開版へ転用しません。過去のLIVE／測定runnerはローカルの認可と終了済みworkを参照するため、再実行しないでください。
