# 校機＋包裝整合｜新對話交接（Waitress 測試階段）

更新日期：2026-11-04

本文件是目前最新交接文件；若與較早的 `NEW_CHAT_HANDOFF_2026-11-04.md` 不一致，以本文件為準。

## 專案與資料保護

- Repository：`Regan1994/Packaging-Inspection`
- 本機路徑：`C:\Users\TJR_\Documents\ChatGPT\Packaging-Inspection`
- 原校機專案 `C:\Users\TJR_\Documents\ChatGPT\校機報工` 必須保持不修改。
- 後續工作只在 `Packaging-Inspection` 進行。
- 正式資料庫在發布包旁的 `instance\inspection.db`，發布包內不得帶入正式 `.db`。

## 目前 Git 狀態

- `main`：`10cb6d4`，未修改。
- 功能分支：`feature/report-detail-demand-date`，提交 `f118257`。
- 備份強化分支：`ops/backup-restore-hardening`，提交 `6bd9fdb`。
- 目前分支：`ops/waitress-integration`，提交 `398a409`。
- 目前工作區乾淨。

## 已完成的功能變更

### 報表明細與需求日期

- 主機 `/reports` 的「查看明細」改用與 `/manager` 相同的明細介面。
- 主機仍保留列印、取消、管理修正等操作。
- 新增需求日期（預計出貨日）修改功能。
- 修改需要管理密碼、修改人與原因。
- 修改前會建立 SQLite 備份，並保留舊值、新值與稽核紀錄。

### 備份與驗證

- 主程式啟動時會依台北日期建立每日自動備份。
- 同一天不重複建立有效備份，預設保留 30 份自動備份。
- `InspectionReporting.exe --backup` 可供 Windows 工作排程每日執行。
- `InspectionReporting.exe --verify-backup <path>` 可驗證指定備份。
- 操作手冊：`docs\backup-restore-runbook.md`。

### Waitress

- 主程式已由 Flask 內建伺服器改為 Waitress。
- 預設仍使用 `0.0.0.0:5000`，現場網址不變。
- 預設 Waitress 執行緒數為 8，可用 `INSPECTION_WAITRESS_THREADS` 調整。
- Waitress 不改變資料庫、畫面、路由或操作流程。
- 目前尚未設定 Windows 服務；仍需直接執行 `InspectionReporting.exe`。

## 測試與發布包

- 完整 pytest：`58 passed, 1 skipped`。
- PyInstaller onedir 打包成功。
- 打包後的 EXE 已在隔離資料目錄實際執行 `--backup` 與 `--verify-backup`，均成功。
- 測試發布包：`dist\InspectionReporting-waitress-20261104.zip`。
- ZIP 大小：約 15 MB。
- SHA-256：`484DCA7ED99B9E255B4CD1E9B3A9BF9D73A10500B8113565BF7D0D52E979B7B8`。
- ZIP 不含 `.db`，只包含程式與空的 `instance\backups` 目錄。

## 主機更新方式

1. 關閉現有 `InspectionReporting.exe`。
2. 完整備份正式 `instance` 資料夾，尤其是 `instance\inspection.db`。
3. 解壓縮新的 onedir ZIP，不要只複製 EXE。
4. 保留正式 `instance\inspection.db` 與 `instance\backups`，不要用發布包空資料夾覆蓋。
5. 啟動新版 `InspectionReporting.exe`。
6. 測試校機、包裝、報表、明細、Excel 與需求日期修改。

主機端不需要另外安裝 Python、Flask、Waitress、PyInstaller 或 `.venv`。

## 現場觀察狀態

- 使用者已確認目前測試功能正常，沒有已知問題。
- 目前先讓 Waitress 版本在現場運行一至兩天觀察壓力。
- 觀察 `instance\app.log`、CPU／記憶體、報表與 Excel、多台電腦同時連線，以及每日備份。
- 觀察期間不要再更新正式主機；若有問題，記錄時間、操作與錯誤訊息。

## 下一階段：Windows 服務化

等待一至兩天觀察完成後，再建立新的分支處理 WinSW：

1. 開機自動啟動。
2. 程式異常自動重啟。
3. 設定服務工作目錄與日誌輪替。
4. 使用低權限服務帳號。
5. 避免服務模式啟動時自動開啟瀏覽器。
6. 建立每日 02:00 的 `--backup` 工作排程。
7. 設定 Windows 防火牆只允許內部區網存取 5000 埠。

不要在現場觀察期間直接修改 `main` 或原校機專案。
