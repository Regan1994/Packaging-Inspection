# 校機＋包裝整合｜新對話交接（Waitress 與備份修正完成）

更新日期：2026-11-05

本文件是目前最新交接文件。若與較早的 `NEW_CHAT_HANDOFF_2026-11-04-WAITRESS.md` 或其他交接文件不一致，以本文件為準。

## 專案與資料保護

- Repository：`Regan1994/Packaging-Inspection`
- 本機路徑：`C:\Users\TJR_\Documents\ChatGPT\Packaging-Inspection`
- 原校機專案 `C:\Users\TJR_\Documents\ChatGPT\校機報工` 必須保持不修改。
- 後續工作只在 `Packaging-Inspection` 進行。
- 正式資料庫位於發布包旁的 `instance\inspection.db`；發布包不得帶入正式 `.db`。

## 目前 Git 狀態

- `main`：`10cb6d4`，未修改。
- 目前分支：`ops/waitress-integration`。
- 最新提交：`a894221 Allow legacy GET backup route`。
- 已推送至：`origin/ops/waitress-integration`。
- GitHub：[ops/waitress-integration](https://github.com/Regan1994/Packaging-Inspection/tree/ops/waitress-integration)
- 交接文件建立前工作區乾淨；新增本文件後若尚未提交，先檢查 Git 狀態再繼續。

## 目前已完成的功能

### 報表明細與需求日期

- 主機 `/reports` 的「查看明細」使用與 `/manager` 相同的明細介面。
- 主機仍保留列印、取消、管理修正等操作。
- 支援需求日期（預計出貨日）修改。
- 修改需要管理密碼、修改人與原因。
- 修改前會建立 SQLite 備份，並保留舊值、新值與稽核紀錄。

### Waitress

- 主程式已由 Flask 內建伺服器改為 Waitress。
- 預設仍使用 `0.0.0.0:5000`，現場網址不變。
- 預設 Waitress 執行緒數為 8，可用 `INSPECTION_WAITRESS_THREADS` 調整。
- 目前尚未設定 Windows 服務；仍直接執行 `InspectionReporting.exe`。
- 使用者已確認 Waitress 版本運行一晚目前沒有問題。

### 備份與驗證

- 主程式啟動時依台北日期建立每日自動備份。
- 同一天不重複建立有效自動備份，預設保留最近 30 份。
- 自動備份檔：`instance\backups\inspection_auto_*.db`。
- 手動命令：

  ```powershell
  .\InspectionReporting.exe --backup
  ```

- 驗證命令：

  ```powershell
  .\InspectionReporting.exe --verify-backup "完整備份檔路徑"
  ```

- 首頁「立即備份」會建立手動備份檔：`instance\backups\inspection_backup_*.db`。
- 網頁備份路由為 `/backup`；為相容現場按鈕與直接導向，最新版本同時接受 `GET` 與 `POST`，已修正原本的 `405 Method Not Allowed`。
- `backup`（網址）與 `backups`（資料夾）是刻意不同的名稱，不要改名資料夾。
- 目前不依賴 Windows 工作排程；現場可使用首頁手動備份或命令列手動備份。

## 最新修正與驗證

- 修正檔案：`app\routes.py`
- 回歸測試：`tests\test_backup_route.py`
- 完整測試結果：`59 passed, 1 skipped`。
- 修正版 onedir 發布包：`dist\InspectionReporting-waitress-backup-fix-20261105.zip`
- SHA-256：`977774CCB87DF72247FB3A672548B7F6F417210F1DAEEEF667216A469A438AD3`
- ZIP 已確認不含任何 `.db` 檔案。
- 使用者已確認修正版目前沒有問題。

## 主機更新方式

若未來需要再次部署新版：

1. 關閉現有 `InspectionReporting.exe`。
2. 完整備份正式 `instance` 資料夾，尤其是 `instance\inspection.db`。
3. 解壓縮完整 onedir ZIP，不要只複製 EXE。
4. 保留正式 `instance\inspection.db` 與 `instance\backups`，不要用發布包內的空資料夾覆蓋。
5. 啟動新版 `InspectionReporting.exe`。
6. 測試首頁備份、校機、包裝、報表、明細與 Excel。

主機端不需要另外安裝 Python、Flask、Waitress、PyInstaller 或 `.venv`。

## 現場觀察狀態

- Waitress 版本已實際運行一晚，使用者回報目前正常。
- 備份首頁按鈕的 405 問題已修正並完成驗證。
- 目前仍建議觀察 `instance\app.log`、CPU／記憶體、報表與 Excel、多台電腦同時連線，以及備份檔是否持續產生。
- 若之前建立的錯誤 Windows 工作排程仍存在，請先停用，避免它重複啟動主程式。
- 觀察期間不要直接修改正式主機；如有問題，記錄時間、操作、網址與錯誤訊息，並先保留 `instance` 備份。

## 下一階段候選工作

等待現場觀察完成後，如仍需要，再建立新的分支處理 Windows 服務化：

1. 開機自動啟動。
2. 程式異常自動重啟。
3. 設定服務工作目錄與日誌輪替。
4. 使用低權限服務帳號。
5. 服務模式啟動時不開啟瀏覽器。
6. 建立每日 02:00 的 `--backup` 工作排程。
7. 設定 Windows 防火牆只允許內部區網存取 5000 埠。

不要在現場觀察期間直接修改 `main` 或原校機專案。

## 新對話開始時

1. 先閱讀本文件。
2. 檢查 `Packaging-Inspection` 的 `git status` 與目前分支。
3. 確認不要碰 `C:\Users\TJR_\Documents\ChatGPT\校機報工`。
4. 等待使用者提出下一個需求；不要自行建立 Windows 服務或修改正式資料。
