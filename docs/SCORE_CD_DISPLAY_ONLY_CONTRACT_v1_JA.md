# CD1 — 返却分布から算出する表示専用スコアの契約

2026-09-20 / policy案の実装指定: `cd-distribution-display-v1`

**状態：今回選択された方針の実装仕様。作成時点ではユーザーPCへ未適用。**

## 1. 変更の根拠と、主張しないこと

提供された原本取得報告では、返却score 2.69、返却分布の期待値2.70の差をJSON解析前の本文で確認している。比較報告はC0／CP／CDの静的比較を示すが、提供元内部の原因・必要な精度保証・意味品質の優劣は確定していない。

ユーザーが指定した次工程は「CD表示限定契約の実装・停止理由表示・新規実素材runの再受入れ」である。本追補は、比較案CDを**人間向けの文脈読取り表示に限って**実装する仕様である。提供元が推奨・保証する契約とは呼ばない。

CDでは、返却分布の期待値をアプリが再現できることを採用理由とする。返却score側が誤り、分布側が意味的に正しいという判断ではない。大差があっても構造が正常なら警告付き候補として通るため、整合性検査による防御がC0より弱くなることを受入れ条件に明記する。

## 2. 適用範囲

| 対象 | 扱い |
|---|---|
| 新しいCD sessionのDominanceReader内の0〜4 Score | この契約で数値を導出し、表示資格を確認する。 |
| EvidenceEvaluator、Noul、Choice、routing | 既存の意味・検証・採用契約を維持。 |
| FieldReducer、root予算、蒸発・UNKNOWN | 変更なし。CDスコアを入力しない。 |
| ContextReader・scheduler | CDスコアやwarningを観測選択・優先度の根拠にしない。既存の停止解除による後続処理は別。 |
| 将来のASR検出／補正、自動制御 | この表示契約での利用は対象外。 |
| 旧C0 run、診断case_01、旧Replay | 当時の記録と採否を維持。新しい公開値へ書き換えない。 |

backendでの数値選択・記録までを実装対象とする。「表示限定」はfrontendだけで数値を差し替えるという意味ではない。

## 3. policyと版

新runのmanifestに`adoption_policy_id=cd-distribution-display-v1`と、その設定hash・実装build・schema／transform版を記録する。要求に対応するpolicyは送信受付前に固定し、後着応答を別policyへ移さない。modelへ新しいpolicy宣言を質問文として混ぜ、応答を都合よく変えようとしない。

新しい標準LIVE起動手順はCD1を明示的に選べるものへ更新する。設定未指定の旧起動・C0 fixture・legacy Replayの扱いは既存C0を保つ。閲覧UIから途中でpolicyを変更しない。

未知のpolicy IDは、黙ってCD1やC0へ読み替えず、該当する新規解析の開始を拒否する。旧schemaの記録からC0と確認できる場合は、その互換規則を固定する。新しいpolicyがあった可能性を判断できない記録は欠損として扱う。

運用の正本は「日本語v0.3＋本追補」。v0.3の§7・13・14・17・19・25・29の該当部分だけを、CD1の新runに限って拡張する。原版v0.3と旧試験の期待結果は保全する。

## 4. 検証・計算の順序

### 4.1 共通のhard検証

現行の、要求／回答の対応、kind、model、question ID、criteria順、legend、候補キー、JSONの形式、重複キーの扱い、型、有限値、範囲、確率合計を維持する。

返却scoreが欠損、bool、文字列、NaN、Inf、0〜4の範囲外なら不適合である。分布が正常でもscoreを推測・補完して救済しない。確率も0〜1の範囲、完全な段階0〜4、対応するlegendを要求し、欠けた確率を0で埋めない。未知の段階数へ自動拡張しない。

### 4.2 確率合計と正規化

合計の許容差は**従来の1e-6のまま**。従来の実装が許容する範囲だけ、記録付きの正規化を行う。合計の許容差と、score対期待値の関係差を混同しない。範囲外の値をclipしない。

正規化前の確率と合計を保存し、正規化した場合は変換係数と正規化後の値も残す。原応答は変更しない。

### 4.3 数値の導出

```text
provider_score = 返却された元score
p[k]           = 返却された段階kの確率（原値）
s              = Σ p[k]
mean_before    = Σ k * p[k]
p_used[k]      = 現行の合計検証と許容内正規化を通した確率
mean_used      = Σ k * p_used[k]
numeric_delta  = provider_score - mean_used
selected_score = mean_used
numeric_candidate_pct = 100 * selected_score / 4
```

内部計算は決定論的に行い、計算方法・版を固定する。独立試験はDecimal/Fraction等で再計算し、実装の浮動小数点との差も記録する。意味を変える丸めを途中へ入れず、丸めは表示でのみ行う。

### 4.4 関係差

既存の関係差判定`abs(numeric_delta) > 1e-6`を、CD1では**診断flag `NUMERIC_DISCREPANCY`**にする。C0側の判定方法と浮動小数点境界を黙って変更しない。境界で数学上の値と実装判定に差があれば試験に残す。

CD1ではこのflagだけではhard error・全体停止にしない。関係検査を実施しなかったり、「一致した」と記録したりはしない。差0.03等の経験値へ合わせた追加の許容上限は導入しない。score4対mean0もwarning付き候補になることを明示的に試験する。

### 4.5 表示資格

数値候補が得られても、公開値が得られるとは限らない。従来どおり次を確認する。

- assessabilityの判定（insufficient／conflicting／同率等ではnull）。
- input_snapshot、profile／schema／rubric版、対象scope／subject、epoch、入力cursor。
- ASR等の依存版、取消し、source時刻、公開済み範囲。
- request期限、readout TTL、順序逆転・旧応答の扱い。

すべての必要条件を満たした場合だけ`value_pct=numeric_candidate_pct`を保存する。それ以外は`value_pct=null`と理由を残す。数値候補をnullの代わりに主表示しない。

unit内に別のhard不良があれば、現行どおりそのunitの公開値はnullとする。別unitの合法なChoice等は従来の規則で保持するが、応答にhard errorがあれば新規送信の全体停止は維持する。

## 5. 新しく記録する情報

以下はアプリ側レコードの意味であり、vendor APIの新しい返却キーではない。既存型との重複は整理してよいが、値の意味を兼用しない。

| 情報 | 必要な内容 |
|---|---|
| policy | 採用policy、validator、transform、buildの版とhash |
| raw | 元応答、要求hash、受信model、unit／questionへの参照 |
| original | provider_score、返却probabilities、legend |
| normalization | 合計、許容差、実施有無、係数、正規化前後平均 |
| relation | numeric_delta、許容差、比較方法、一致／不一致／検算不能 |
| selection | selected_source=`returned_distribution`、selected_score |
| publication | 公開値またはnull、表示資格、理由、as-of、TTL |
| limitations | `usage_scope=display_only`、warning_codes、欠損事項 |

元`raw_score`等の既存キーに、分布由来の値を黙って上書きしない。原応答とアプリ派生値を別構造にする。confidenceを選択値に混ぜない。

不一致warningと、後でstaleになった状態は同時に保存できるものとする。公開値の失効で、不一致の原記録や停止原因を消さない。

## 6. 動作・停止の判定表

| 状態 | 公開readout | 新規Jev送信 |
|---|---|---|
| C0で関係差不適合 | 従来のerror/null | 従来の全体停止 |
| CD1で構造合法、関係差のみ不適合、表示資格あり | 分布由来の値＋不一致警告 | この差だけでは停止しない |
| CD1で数値合法、assessability不足 | null＋不足理由。warningがあれば保持 | 不足だけで全体停止しない。従来規則 |
| CD1で期限切れ・旧依存版 | 現在値はnull／採用せず履歴に保持 | 従来の正常棄却規則 |
| HTTP、JSON、型、範囲、legend、確率合計などのhard error | 現行のunit不良処理 | 従来の全体停止 |
| Stop、許可／予算／排他不成立 | 実状態を記録 | 停止。暗黙の再開・再試行なし |

warningを例外から外す変更と、hard error時の停止範囲を縮小する変更は別である。今回は前者だけを行う。

## 7. Viewer表示

主画面はH1.2の配置を維持する。

- 新CD readoutには、採用元が分かる短い`分布から算出`表示。不一致時だけ`数値不一致`も出す。
- 両値・差・正規化・policy・期限は選択行の技術情報へ置く。主画面へJSONや長い注意書きを戻さない。
- 非排他Scoreを合計100%へ正規化しない。Choice・場の濃度と混ぜない。
- evaluationのみから「当時公開」を作らない。保存されたreadoutの値とpolicyを使う。
- 診断case_01には本番snapshotがないので、67.50を任意の動画の公開値として表示しない。

上段の全体状態は、現在のcursorまでの記録から作る。表示カード数、静止時間、最後の警告だけを停止の証拠にしない。通常画面には一行だけ置き、停止原因・時刻・派生元は詳細から追えるようにする。

終了eventの後でも、途中停止があったなら「終了（途中停止：…）」等で原因を保持する。終了と成功を同義にしない。旧ログに終了状態がなければ「この記録には以後の評価がありません／状態不明」等とし、正常終了を捏造しない。

## 8. H1.2の評価済み整理

下段から行を整理する条件は変更しない。有効な直接Evidence評価がその観測版の有限の対象集合に揃った場合だけ整理する。反証／情報不足という合法な結論は評価完了に含める。

CDのDominanceが表示されたこと、その入力snapshotに原観測が含まれたこと、同じ文字や時刻があることを、直接評価済みの証拠にしない。部分完了、計測のみ、旧runの対応不明、確認中の行は従来どおり保持する。

## 9. 旧Replayと復帰

旧C0 runには新policyも新readoutも書き込まない。新CD runは新policyを記録して保存し、Replayではそのrunの履歴を再現する。表示用の正規化やpolicyを現在設定で再実行して過去値を変えない。

C0へ戻す場合は、次の新sessionにC0を明示する。実行中sessionへ差し替えず、CDで作った記録も削除しない。コードを戻す必要がある場合は、変更前の限定backupを正常停止後に照合して復元する。予算や元runを巻き戻さない。

## 10. 根拠資料と新しい設計判断

- 原本の数値差と内部原因未確定：`references/SCORE_RAW_CAPTURE_RESUME_REPORT_JA.md`。
- 55件比較、数値候補と表示資格、大差を通すリスク：`references/SCORE_ADOPTION_CONTRACT_COMPARISON_REPORT_JA.md`。
- H1.2の実装・直接評価との対応・非LIVE境界：`references/HUMAN_DEBUG_VIEWER_H12_REPORT_JA.md`。
- 基礎の時刻・場・来歴・予算：`references/JEV_VIDEO_CONTEXT_FIELDS_DESIGN_v0_3_JA.md`。

上記は提供資料に基づく事実。本追補のpolicy ID、適用指定、状態・記録項目、初回実行枠は今回の実装用仕様であり、提供元の標準機能や実測結果ではない。
