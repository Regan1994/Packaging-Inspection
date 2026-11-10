# 校機＋包裝整合｜新對話交接（CTU 包裝與作業紀錄修正）

更新日期：2026-11-10（Asia/Taipei）

本文件是目前最新交接；若與較早的交接文件衝突，以本文件為準。

## 專案與分支

- Repository：`Regan1994/Packaging-Inspection`
- 本機路徑：`C:\Users\TJR_\Documents\ChatGPT\Packaging-Inspection`
- 目前工作分支：`ops/waitress-integration2`
- 分支起點：`7d8cf3f feat: clarify order removal and backfill packing`
- GitHub 已存在的整合分支：`origin/ops/waitress-integration`（`7d8cf3f`）
- `ops/waitress-integration2` 尚未提交、尚未推送到 GitHub。
- 不要修改 `main`。

## 使用者工作規則

- 任何檔案、分支、提交、推送、打包或部署修改，都必須先取得使用者明確同意。
- 僅在上述 `Packaging-Inspection` 專案操作。
- 訂單號碼不可修改。
- 訂單基本內容（客戶、機台、數量、需求日期等）若填錯，維持「刪除訂單後用相同單號重建」的流程；不要恢復直接修改基本內容的頁面入口。
- 正式主機更新時，必須保留原有 `instance\inspection.db` 與 `instance\backups`，不可用發布包內資料夾覆蓋。

## 本輪已完成、尚未提交的程式修改

### 版次與訂單操作

- `config.py` 版次已更新為 `V801.11.10`。
- 訂單明細的「訂單取消」說明標題已改為「業務取消訂單」。

### CTU／未經校機的包裝掃描

- 待校機訂單掃描包裝 `START` 時，不再因尚未校機完成而被拒絕。
- 系統會自動補登校機完成：校機 `START` 與 `FINISH` 都寫為掃描當日的台北時間 `00:00:00`。
- 隨後正常建立包裝開始紀錄；同一筆包裝再掃 `FINISH` 時正常完成包裝。
- 校機已在進行中的訂單仍會拒絕包裝掃描，避免覆寫真實進行中的校機紀錄。
- 稽核動作：`auto_complete_inspection_for_packing`；訂單明細的修正紀錄會顯示「因包裝掃描自動完成校機」。

### 作業紀錄修正整合

- 訂單明細已移除「修改需求日期」與獨立「補登開始時間」的頁面／路由入口。
- 原「管理員錯誤修正」已改名為「作業紀錄修正」。
- 校機作業修正保留校機人員、START、FINISH 的補登／更正功能；待校機訂單可由此補登 START。
- 新增包裝作業修正，可修正包裝狀態、包裝人員、包裝 START 與 FINISH。
- 校機與包裝修正都要求修改人、修改原因、管理密碼；先建立資料庫備份，並保留前後值與稽核紀錄。
- 缺料／補料／人工暫停區塊與修正方式維持原本設計。

## 驗證結果

- 專案 `.venv` 執行完整測試：`61 passed, 1 skipped`。
- 相關的包裝與作業修正測試：`9 passed`。
- 已執行 `git diff --check`，無格式錯誤。

## 發布包

- 最新發布 ZIP：`dist\InspectionReporting-waitress-integration2-v801-20261110.zip`
- ZIP 內含：`InspectionReporting.exe`
- ZIP 不含任何 `.db` 檔案。
- SHA-256：

  ```text
  D4CCF1E8D810E15017C3FA1500F719145107FE095C34AE2202744BD8858893A0
  ```

## 目前 Git 狀態注意事項

- 程式修改與本交接文件都尚未提交、尚未推送。
- 較早的未追蹤交接文件 `docs\NEW_CHAT_HANDOFF_2026-11-09-ORDER-PACKING.md` 仍保留，請勿自行刪除或提交，除非使用者另有指示。

## 新對話開始時

1. 先讀本文件。
2. 確認路徑是 `C:\Users\TJR_\Documents\ChatGPT\Packaging-Inspection`，分支為 `ops/waitress-integration2`。
3. 執行 `git status --short --branch`，保留既有未提交修改。
4. 不要修改 `main`、不要自行提交、推送、重新打包或部署。
5. 等待使用者提出下一個需求。
