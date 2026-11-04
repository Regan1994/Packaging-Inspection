# 備份與還原操作流程

本文件說明 `Packaging-Inspection` 在 Windows 主機上的備份、驗證與還原流程。正式資料庫位於發布包旁的 `instance\inspection.db`，不要把正式 `instance` 覆蓋成發布包內的空資料夾。

## 1. 自動備份

主程式正常啟動時會建立當日自動備份；同一天不重複建立，並保留最近 30 份 `inspection_auto_*.db`。

若主機長時間不重啟，請用 Windows 工作排程每日執行：

```text
InspectionReporting.exe --backup
```

建議排程在每天 02:00 執行，執行目錄設定為發布包目錄，並使用與主程式相同的資料目錄。指令完成時會回傳成功狀態，備份檔會先使用 SQLite backup API 建立，再執行 `integrity_check`。

## 2. 驗證備份

驗證指定備份檔：

```text
InspectionReporting.exe --verify-backup "C:\TJR\InspectionReporting\instance\backups\inspection_auto_20261104_020000_ab12cd34.db"
```

看到 `backup verification passed` 才表示檔案可由 SQLite 開啟且完整性檢查通過。

## 3. 更新前備份

1. 暫停現場使用。
2. 建立一次手動備份，或確認當日自動備份已存在並通過驗證。
3. 另外複製整個正式 `instance` 資料夾到外部磁碟或 NAS。
4. 保留目前發布包，將新版本放到新的版本資料夾。
5. 更新時只替換程式檔案，不覆蓋正式 `instance`。

## 4. 還原流程

正常還原使用系統管理頁的「還原資料庫」功能。系統會先建立目前資料庫的安全備份，再驗證來源備份，最後以暫存檔替換正式資料庫。

還原前請先：

1. 停止所有掃描與報表操作。
2. 確認來源備份已通過 `--verify-backup`。
3. 記錄來源檔名與還原原因。
4. 還原完成後重新啟動主程式。
5. 依序確認首頁、校機掃描、包裝掃描、報表與 Excel 匯出。

## 5. 還原演練

每月至少一次，把備份還原到測試資料夾，不要直接覆蓋正式資料庫，確認：

- SQLite `integrity_check` 通過。
- 訂單、人員與機型資料可查詢。
- 報表與 Excel 可以開啟。
- 正式 `instance` 沒有被修改。
