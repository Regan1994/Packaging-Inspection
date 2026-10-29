# TJR 校機報工系統｜新對話接手文件

更新日期：2026-10-29（Asia/Taipei）

## 新對話先讀這裡

這是目前最新的接手文件。新對話開始時，請先閱讀本文件，再檢查 Git 狀態與相關程式碼；不要只依照舊版 handoff 或最後一筆提交判斷現況。

專案原始碼：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工
```

正式現場安裝位置：

```text
C:\TJR\InspectionReporting
```

正式資料庫位於現場電腦的 `instance/inspection.db`。任何更新都必須保留正式 `instance`，不可用打包包內的空白資料庫覆蓋現場資料。

## 目前狀態

- 系統：Flask + SQLite 的區域網路校機報工主系統。
- 目前開發分支：`codex/material-shortage-pause`。
- 工作目錄目前有 V406 修改，尚未 commit／push。
- 之前已推送的基準提交：`0372318 Add report pagination and scheduled work time`。
- 目前版本：`V406.10.29`。
- V406 尚未重新打包；目前 `dist\InspectionReporting` 仍是上一版發布包。
- 最新測試：`27 passed, 1 skipped`。
- 最新打包輸出：`C:\Users\TJR_\Documents\ChatGPT\校機報工\dist\InspectionReporting`。
- 執行檔：`C:\Users\TJR_\Documents\ChatGPT\校機報工\dist\InspectionReporting\InspectionReporting.exe`。

開始任何修改前先執行：

```powershell
Set-Location -LiteralPath 'C:\Users\TJR_\Documents\ChatGPT\校機報工'
git status --short --branch
git log -3 --oneline --decorate
```

不要任意還原、刪除或覆蓋使用者既有變更。

## V405.10.29 本次修改（前一版）

使用情境：機台已到現場可以開始校驗，但正式訂單流程尚未完成，現場無法掃 START。

已在訂單編輯頁加入「補登開始時間」：

1. 先建立訂單。
2. 在訂單編輯頁的「補登開始時間」區塊選擇校機人員。
3. 輸入實際開始日期與時間、原因及操作人員。
4. 送出後，訂單會由「待校機」改為「校機中」，並記錄實際開始時間。
5. 後續可照原流程掃描 FINISH 完成訂單。

限制：

- 只可對「待校機」訂單補登。
- 校機人員必須是啟用中的人員。
- 不接受未來時間。
- 輸入時間以台北時間解讀，資料庫仍以 UTC 儲存。
- 會新增稽核紀錄 `manual_start`，包含訂單、員工、時間、原因及操作人員。
- 一般掃描 START 流程不受影響。

本次主要修改檔案：

```text
app/routes.py
app/services.py
config.py
static/app.css
templates/inspection_models.html
templates/inspection_scan.html
templates/inspection_order_edit.html
tests/test_inspection.py
docs/inspection-current-status.md
docs/version-history.md
docs/versioning.md
docs/user-guide.md
docs/NEW_CHAT_HANDOFF_2026-10-28.md
docs/NEW_CHAT_HANDOFF_2026-10-29.md
```

## V406.10.29 本次修改

本次依現場回饋調整掃描頁與機型主檔操作介面：

- 掃描頁上方區塊加寬，與下方「掃描操作說明」對齊。
- 條碼輸入與送出掃描區佔約 2/3，右側約 1/3 放置列印入口，降低誤觸掃描流程的機會。
- 列印入口文字統一為「列印缺料/補料條碼」。
- 機型主檔操作欄加寬，長機型名稱輸入框固定約 280px，保存與刪除按鍵靠右排列。
- 首頁版本顯示更新為 `V406.10.29`。

驗證結果：`27 passed, 1 skipped`；V406 尚未重新打包。

## 已完成的相關功能

- 建立訂單、機型、人員與條碼列印。
- 員工條碼 → 訂單 START／FINISH 掃描流程。
- 缺料／補料流程，缺料期間停止累計實際工時。
- 報表訂單明細每頁顯示 25 台，右下角可分頁。
- 報表明細移除顯示中的「軸別」欄位，但搜尋仍保留軸別條件。
- 報表明細加入「校機建立日期」，只顯示年月日、不顯示時間。
- 報表與 Excel 顯示總經過時間、缺料時間，以及平日 08:00～17:00、扣除 12:00～13:00 午休的正常工時。
- 目前不計算晚上、週末與加班工時；加班規則尚未實作。
- 掃描器 AIM 前綴／控制字元清理與舊條碼相容。
- `V404.10.28` 起相容掃描器受 Caps Lock／鍵盤狀態影響輸出的英文字母大小寫。
- 重要刪除操作前會備份並保留稽核紀錄。

## 目前仍需注意的問題

- 使用者先前回報過：建立新訂單時輸入新的製號與機型，可以產生條碼，但訂單清單沒有顯示；手動建立機型後，再建立一次相同訂單，沒有跳出「訂單已存在」，反而再次產生條碼，之後清單才出現。現場暴力測試暫時未重現，後續若再出現，先記錄完整操作順序、訂單狀態、機型是否存在、資料庫內容與畫面訊息。
- 加班尚未納入工時計算。現行規則只計算正常班，程式關閉也不應被當成正式的下班或加班記錄。
- 新增補登開始時間後，需要在現場驗證：補登後 FINISH、報表工時、缺料狀態及稽核紀錄是否符合需求。

## 測試與打包

修改後至少執行：

```powershell
python -m pytest -q --basetemp .pytest-tmp
python -m compileall -q app tests
git diff --check
```

重新打包：

```powershell
.\build_exe.ps1
```

這是 PyInstaller `onedir` 包裝，部署時必須複製整個 `InspectionReporting` 資料夾，不能只複製 EXE。打包資料夾內不應帶入開發用 `inspection.db`。

## 開發環境啟動

```powershell
Set-Location -LiteralPath 'C:\Users\TJR_\Documents\ChatGPT\校機報工'
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1
python app.py
```

主機本機：`http://127.0.0.1:5000/`

區網其他電腦：`http://主機IPv4:5000/`

程式預設監聽 `0.0.0.0:5000`。正式使用時只能讓一台主機執行程式並保存唯一資料庫。

## 現場更新資料保留規則

1. 關閉現場 `InspectionReporting.exe`。
2. 備份 `C:\TJR\InspectionReporting\instance`。
3. 將舊安裝資料夾改名保留。
4. 複製新版完整 `InspectionReporting` 資料夾。
5. 移除新版空白 `instance`。
6. 將舊版的正式 `instance` 複製回新版。
7. 啟動新版，確認訂單、人員、機型及報表資料仍存在。

不可直接覆蓋或刪除正式 `instance/inspection.db`。

## 重要檔案

- 路由與頁面流程：`app/routes.py`
- 商業邏輯：`app/services.py`
- 資料庫初始化／升級：`app/db.py`
- 條碼處理：`app/barcode.py`
- 訂單編輯頁：`templates/inspection_order_edit.html`
- 自動測試：`tests/`
- 打包腳本：`build_exe.ps1`
- 版本歷史：`docs/version-history.md`
- 目前狀態：`docs/inspection-current-status.md`

## 可直接貼到新對話的開場訊息

```text
請接手「TJR 校機報工系統」。
專案位於：C:\Users\TJR_\Documents\ChatGPT\校機報工

請先完整閱讀：
docs/NEW_CHAT_HANDOFF_2026-10-29.md

目前版本是 V406.10.29。除了 V405 的「補登開始時間」功能外，V406 也調整了掃描頁版面、列印缺料/補料條碼文字，以及機型主檔長機型名稱輸入框。

目前 V406 修改尚未 commit／push，也尚未重新打包。請先檢查 git status、現有程式碼與測試，再依我接下來回報的問題處理；不要自行覆蓋正式 instance 資料。
```
