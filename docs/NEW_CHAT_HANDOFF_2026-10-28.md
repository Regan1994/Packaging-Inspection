# TJR 校機報工系統｜新對話接手文件

> 最新交接文件請改讀：[NEW_CHAT_HANDOFF_2026-10-29.md](NEW_CHAT_HANDOFF_2026-10-29.md)。本文件保留作為前一版交接紀錄。

更新日期：2026-10-28（Asia/Taipei）

## 新對話先讀這裡

這是目前專案的主要接手文件。新對話開始時，請先閱讀本文件，再檢查 Git 狀態與相關程式碼；不要只依照較舊的 handoff 文件判斷現況。

專案原始碼位置：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工
```

正式使用電腦的預定安裝位置：

```text
C:\TJR\InspectionReporting
```

使用者已將目前版本安裝到現場並開始上工。後續以「發現問題後修改、測試、重新打包、保留正式資料更新」為主要工作方式。

## Git 與目前檔案狀態

- 目前開發分支：`codex/material-shortage-pause`
- 開發基底／現場版本分支：`codex/final-inspection`
- 遠端現場版本分支：`origin/codex/final-inspection`
- 已推送提交：`37a4467 Add project handoff and user installation guide`
- 2026-10-29 工時規則、報表分頁與補登開始時間修改後測試結果：`27 passed, 1 skipped`
- Word 說明書與本接手文件已納入 Git；缺料暫停功能目前在開發分支修改中，尚未提交或推送。
- 目前工作目錄仍是 dirty state；缺料／補料、掃描頁說明、版本顯示及 `V404` 掃描相容修正都尚未 commit／push。新對話不可只依照最後一筆提交判斷功能。

開始修改前先執行：

```powershell
git status --short --branch
git log -3 --oneline --decorate
```

請保留使用者既有變更，不要任意還原或刪除未追蹤文件。

## 系統用途與架構

- Windows 區域網路內使用的 Flask + SQLite 校機報工系統。
- 一台主機執行程式並保存唯一資料庫，其他電腦以瀏覽器連線。
- 主機本機網址：`http://127.0.0.1:5000/`
- 區網其他電腦網址：`http://主機IPv4:5000/`
- 程式預設監聽：`0.0.0.0:5000`
- 正式資料庫：`instance/inspection.db`
- 自動／手動備份：`instance/backups/`
- 不可在多台電腦各自執行一份程式，否則會形成互不相通的資料庫。

## 已完成的主要功能

- 首頁六個主要功能入口；建立訂單與校機掃描位於第一排各半寬。
- 機型主檔前台只顯示名稱，內部代碼由系統自動產生。
- 機型、人員可編輯；符合條件者可刪除或停用。
- 員工編號最多四碼，員工條碼由系統產生。
- 建立訂單時使用唯一訂單號碼、機型與機台製號。
- 同一機台製號可在不同訂單重複使用。
- 軸別自動判斷：機型名稱以 `F`／`M` 開頭，或製號以 `S` 開頭，判定為五軸；其他為四軸。
- 四軸／五軸總數為全體固定統計，不會因報表篩選而錯誤歸零。
- 掃描流程：員工條碼 → 訂單 START／FINISH 條碼。
- 缺料／補料流程：員工條碼 → 固定缺料或補料條碼 → 該訂單 START 條碼。
- 誤掃人員可直接掃正確員工覆蓋，或按掃描頁的「清除／更換人員」。掃描頁提供 START、FINISH、缺料與補料四格操作說明；首頁左下角顯示版本 `V405.10.29` 供部署確認。
- `V402.10.28` 增加掃描器 AIM 前綴／控制字元清理、既有資料庫條碼直接比對與舊員工條碼相容；若部署時讀到空白資料庫，掃描頁會提示檢查 `instance` 資料夾位置。
- `V404.10.28` 相容掃描器受 Caps Lock／鍵盤狀態影響而輸出的小寫系統條碼，員工與訂單標籤不必重印。
- 版本格式為 `V主版本＋兩位次版本.MM.DD`。建立新分支時主版本加一、次版本從 `01` 開始；同一分支更新時次版本依序增加，日期使用該次更新日期。例如 `V401.10.28`、`V402.10.30`，下一個新分支從 `V501.MM.DD` 開始。
- 缺料期間停止累計實際工時，可記錄多段缺料區間；缺料中必須先補料才能 FINISH。
- 訂單狀態：待校機、校機中、已完成、已取消。
- 報表頁面不顯示員工編號，也沒有日期起／日期迄篩選。
- 報表下方有訂單明細，可進入編輯頁執行列印 Code、取消與安全刪除。
- 僅尚未開始的誤建訂單可刪除；歷史訂單保留。
- 人員若已有歷史訂單不可刪除，應改用停用。
- Excel 匯出包含人員、軸別與訂單明細統計。
- 報表與 Excel 顯示總經過時間、缺料時間及依平日 08:00～17:00 正常班計算的實際校機工時；暫扣除 12:00～13:00 午休，加班尚未納入。
- 訂單編輯頁可對待校機訂單補登實際開始時間、校機人員與原因，之後可照原流程 FINISH。
- 訂單與員工條碼以 60×60 mm 標籤為目標。
- 重要刪除操作前會建立備份並保留稽核紀錄。

## 開發環境啟動

PowerShell：

```powershell
Set-Location -LiteralPath 'C:\Users\TJR_\Documents\ChatGPT\校機報工'
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1
python app.py
```

若需重新建立環境：

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt pyinstaller
```

## 修改後驗證

至少執行：

```powershell
python -m pytest -q --basetemp .pytest-tmp
python -m compileall -q app tests
git diff --check
```

目前基準結果：

```text
27 passed, 1 skipped
```

`skipped` 是已被目前校機流程取代的舊組裝測試，不是失敗。

## 重新打包

在已啟用 `.venv` 的 PowerShell 執行：

```powershell
.\build_exe.ps1
```

輸出資料夾：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工\dist\InspectionReporting
```

執行檔：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工\dist\InspectionReporting\InspectionReporting.exe
```

這是 PyInstaller `onedir` 包裝，部署時必須複製整個 `InspectionReporting` 資料夾，不能只複製 EXE。

全新打包資料夾的 `instance` 應只有空的 `backups` 資料夾，不應包含 `inspection.db`，以免把開發資料帶到現場。

目前最新打包版本為 `V405.10.29`，已確認：

- `dist/InspectionReporting/InspectionReporting.exe` 存在。
- 包內沒有任何 `.db`。
- 包內已建立空白的 `instance/backups/`。
- 使用者現場掃描器曾把 `IW100000006F7C7` 輸出成小寫 `iw100000006f7c7`；切回大寫後現場立即恢復正常，確認問題是掃描器／Caps Lock 大小寫，不是員工資料損壞。
- `V404.10.28` 已在程式端自動相容大小寫，但使用者尚未在現場回報新版部署驗收結果。

## 新對話優先事項

1. 先確認使用者是否已把 `V404.10.28` 更新到現場，並用小寫輸出狀態實測員工、START、FINISH、缺料與補料條碼。
2. 若現場測試通過，再依使用者指示 commit／push `codex/material-shortage-pause`；目前不要假設已推送。
3. 若仍有條碼問題，先取得掃描頁下方的完整「掃描內容」與畫面訊息，不要要求重建員工或訂單。
4. 更新／測試時保留正式 `instance`，不得把打包資料夾的空白資料庫取代現場資料。

## 現場更新方式（重要）

正式資料全部位於：

```text
C:\TJR\InspectionReporting\instance
```

更新時必須保留整個 `instance` 資料夾：

1. 關閉現場的 `InspectionReporting.exe`。
2. 備份 `C:\TJR\InspectionReporting\instance`。
3. 將舊資料夾改名為 `InspectionReporting_舊版`。
4. 把新版完整資料夾複製成 `C:\TJR\InspectionReporting`。
5. 移除新版的空白 `instance`。
6. 把 `InspectionReporting_舊版\instance` 完整複製回新版。
7. 啟動新版並確認訂單、人員、機型及報表資料仍存在。
8. 正常使用幾天後再決定是否刪除舊版；備份建議繼續保留。

任何更新都不可直接覆蓋或刪除正式的 `instance/inspection.db`。

## 新電腦首次安裝摘要

- 建議安裝於 `C:\TJR\InspectionReporting`，不要放在 `Program Files`。
- 目標電腦不需安裝 Python。
- 若 SmartScreen 出現警告，確認是自己打包的版本後，選「其他資訊」→「仍要執行」。
- Windows 防火牆只允許「私人網路」，不要開放公用網路。
- 第一次啟動會建立新的 `instance/inspection.db`。
- 初始管理密碼為 `TJR`，正式使用後應到設定頁修改。

## 現場操作與維護注意事項

- 主機程式關閉時，其他電腦無法使用系統。
- 不要把 `InspectionReporting` 放在唯讀或需要系統管理員權限才能寫入的位置。
- 掃碼器應使用一般 USB 鍵盤輸入模式（HID），通常設定為掃描後送出 Enter。
- 掃描器可能受 Caps Lock／鍵盤狀態影響，把條碼英文字母輸出成小寫；`V404.10.28` 起會自動相容本系統條碼大小寫。
- 標籤列印選擇 60×60 mm、實際大小／100%。
- 修改資料庫結構時，需考慮既有正式資料庫升級，不能只驗證全新資料庫。
- 修改刪除、取消、還原、資料庫遷移等功能前，先備份並新增回歸測試。
- 現場回報問題時，先記錄操作頁面、輸入內容、畫面訊息、訂單狀態與發生時間，再重現與修正。

## 重要檔案索引

- 應用程式入口：`app.py`
- Flask 建立與初始化：`app/__init__.py`
- 路由與頁面流程：`app/routes.py`
- 商業邏輯：`app/services.py`
- 資料庫初始化／升級：`app/db.py`
- 條碼處理：`app/barcode.py`
- HTML：`templates/`
- CSS／JavaScript：`static/`
- 自動測試：`tests/`
- 打包腳本：`build_exe.ps1`
- 使用者說明：`docs/user-guide.md`
- 管理說明：`docs/admin-guide.md`
- 目前實作狀態：`docs/inspection-current-status.md`
- Word 使用與安裝說明：`docs/TJR校機報工系統使用與安裝說明.docx`

## 可直接貼到新對話的開場訊息

```text
請接手「TJR 校機報工系統」。專案位於：
C:\Users\TJR_\Documents\ChatGPT\校機報工

請先完整閱讀：
docs/NEW_CHAT_HANDOFF_2026-10-28.md

目前現場已開始使用，正式資料在現場電腦：
C:\TJR\InspectionReporting\instance

任何更新都必須保留正式 instance 資料夾。請先檢查 git status、現有程式碼與測試，再依我接下來回報的問題處理；不要先自行更動功能。
```

