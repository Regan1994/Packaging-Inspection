# 校機報工系統 V501 維護交接文件

更新日期：2026-11-02

這份文件供下一個 Codex 視窗接手後續維護。開始修改前，請先閱讀本文件並檢查 Git 狀態；不要操作正式 SQLite，也不要覆蓋現場 `instance`。

## 專案與 Git 狀態

- Repository：`Regan1994/inspection-reporting-barcode`
- 本機路徑：`C:\Users\TJR_\Documents\ChatGPT\校機報工`
- 目前分支：`codex/manual-pause-v501-11-02`
- 遠端分支：`origin/codex/manual-pause-v501-11-02`
- 最新 commit：`2f0d310 Add manual inspection pause workflow`
- 目前版本：`V501.11.02`
- 建立本文件前工作區為乾淨狀態，本機與遠端同步。

## V501 已完成功能

### 人工暫停

暫停流程：

```text
原認領員工條碼 → 固定暫停條碼 → 原訂單 START
```

恢復流程：

```text
原認領員工條碼 → 原訂單 START
```

規則：

- 人工暫停期間仍計入總經過時間。
- 人工暫停期間不計入實際校機工時。
- 暫停中禁止 FINISH，必須先恢復。
- 人工暫停與缺料暫停分開保存。
- 報表、主管 `/manager`、訂單明細與 Excel 均顯示人工暫停時間。
- 掃描頁可列印固定暫停條碼。

### 測試與打包

- 完整測試：`31 passed, 1 skipped`
- 發布資料夾：`dist\InspectionReporting`
- ZIP：`dist\InspectionReporting-manager.zip`
- ZIP SHA-256：`5B1BF679CBC468C10746798EDD9FF18C4C02BD5BA3A2EF2B997F8AC1FBF3E081`
- 發布包內沒有 `inspection.db`，只有空的 `instance\backups`。

## 正式部署安全規則

正式資料庫位於現場主機的：

```text
InspectionReporting\instance\inspection.db
```

更新時必須：

1. 關閉舊 `InspectionReporting.exe`。
2. 備份舊版完整 `instance`。
3. 解壓縮新版完整 onedir 發布包，不可只複製 EXE。
4. 將正式 `instance` 帶回新版資料夾。
5. 只啟動一個 `InspectionReporting.exe`。
6. 測試建立、START、人工暫停、恢復、FINISH 與 `/manager`。

禁止：

- 用空白 `instance` 覆蓋正式資料庫。
- 直接修改、清除或還原正式 SQLite。
- 同時執行新舊兩套主系統。
- 將 `instance` 或發布 ZIP 加入 Git。

## 下一階段維護方向

依投入成本與實際效益排序：

### 1. 防止重複執行

- Windows 主機只允許一個主系統程序。
- 第二次開啟時提示系統已運行，必要時只開啟既有網址。
- 不要用刪除 lock file 等不安全方式破壞正常啟動。

### 2. 自動備份

- 系統啟動時檢查當日是否已自動備份。
- 同一天只建立一次自動備份。
- 使用 SQLite backup API，不直接複製使用中的資料庫。
- 建議只保留最近 30 份自動備份，手動備份不得被自動刪除。
- 備份失敗應記錄 log，但不應阻止主系統啟動。

### 3. 條碼防止跨資料庫誤認

- 現有短條碼空間有限，不優先增加長度。
- 可將資料庫／安裝識別碼加入 checksum 計算，維持現有條碼長度。
- 必須保留既有已列印條碼相容性。
- 新條碼若拿到另一份資料庫掃描，應明確顯示資料庫不相符，不可誤認成相同流水 ID 的舊訂單。

### 4. 管理員補登與錯誤修正

- 沿用目前管理密碼架構，不建立複雜帳號角色系統。
- 管理員可修正 START、FINISH、缺料、補料、人工暫停、恢復與誤掃人員。
- 每次修改要求密碼與修改原因。
- 修改前自動備份。
- 保留舊值、新值、修改人、修改時間與原因，不直接刪除歷史。

### 5. 報表管理

優先考慮：

- 每日／每月每位員工實際校機工時。
- 各機型平均校機時間。
- 缺料與人工暫停時間統計。
- 進行中、暫停中與逾時未完成訂單。
- 月報 Excel。

## 新視窗建議開場訊息

```text
請先閱讀 C:\Users\TJR_\Documents\ChatGPT\校機報工\docs\handoff-maintenance-v501-2026-11-02.md，
再檢查目前 Git 分支與工作區。請保留正式 instance，不要修改或覆蓋正式 SQLite。
目前要開始下一階段維護，先從「防止重複執行」與「自動備份」中確認本次範圍，建立新分支後再修改。
```
