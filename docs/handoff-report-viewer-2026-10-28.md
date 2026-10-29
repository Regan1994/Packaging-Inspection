# 校機報工報表閱讀器／主系統交接文件

更新日期：2026-10-28

這份文件供下一個 Codex 視窗在修 bug 前閱讀。先確認 bug 是閱讀器啟動器問題，還是主系統 `/manager` 報表頁問題。

## 專案關係

### 主系統

- Repository：[Regan1994/inspection-reporting-barcode](https://github.com/Regan1994/inspection-reporting-barcode)
- 本機路徑：`C:\Users\TJR_\Documents\ChatGPT\校機報工`
- 作用：Flask 網頁伺服器、正式 SQLite、訂單、機型、校機人員、校機狀態與缺料暫停資料
- 伺服器電腦：`192.168.0.193`
- 目前使用的是打包好的 `InspectionReporting.exe`
- 分支：`codex/material-shortage-pause`
- 最近 commit：`200df21 Add read-only manager report entry`

### 閱讀器

- Repository：[Regan1994/inspection-report-viewer](https://github.com/Regan1994/inspection-report-viewer)
- 本機路徑：`C:\Users\TJR_\Documents\ChatGPT\校機報工閱讀器`
- 作用：Windows 啟動器，只開啟主系統 `/manager`
- 不執行 Flask
- 不連線 SQLite
- 不建立第二份資料庫
- 不修改主系統資料
- 分支：`main`
- 最近 commit：`5578205 Create read-only report viewer launcher`

## 實際部署架構

```text
192.168.0.193 主機
  └─ InspectionReporting.exe
       └─ http://192.168.0.193:5000/manager

業務經理電腦
  └─ ReportViewer.exe
       └─ Edge／Chrome App Mode
            └─ http://192.168.0.193:5000/manager
```

已在五台電腦實際測試，均可使用。

## 主系統 `/manager`

相關檔案：

- `app/routes.py`
- `templates/manager.html`

入口：

```text
GET http://192.168.0.193:5000/manager
```

目前頁面功能：

- 訂單號碼、機型、機台製號、校機人員與狀態查詢
- 判斷待校機、校機中、缺料暫停、已完成、已取消
- 單筆訂單明細
- 開始／結束時間
- 缺料／停工區間與缺料時間
- 既有實際校機工時

唯讀限制：

- `/manager` 只接受 GET
- POST `/manager` 應回傳 405
- 不提供建立、編輯、取消、刪除、列印、設定或條碼管理
- 直接使用既有 `inspection_order` 與 `inspection_material_pause` 資料表
- 不新增資料庫表

## 實際校機工時決策

目前保留主系統既有工時計算，不在閱讀器端重新計算。

目前顯示方式使用主系統既有：

```text
正常工作時段內的經過時間 - 缺料暫停時間 = 實際校機工時
```

目前正常工作時段暫定為平日 08:00～17:00，12:00～13:00 午休不計；晚上、週末與尚未定義的加班不計入。若日後要改，先確認加班、停工重疊、未結束區間與缺料定義。

## 閱讀器建置與使用

原始碼專案：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工閱讀器
```

伺服器網址設定檔 `ReportViewer.json` 內容應為：

```json
{
  "serverUrl": "http://192.168.0.193:5000/manager"
}
```

建置：

```powershell
cd "C:\Users\TJR_\Documents\ChatGPT\校機報工閱讀器"
powershell -ExecutionPolicy Bypass -File ".\build.ps1"
```

輸出：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工閱讀器\publish\win-x64\ReportViewer.exe
```

部署給經理時，必須複製整個 `publish\win-x64` 資料夾，不可只複製 EXE。經理電腦不需要 Python、SQLite 或 .NET Runtime，但必須能連到 `192.168.0.193`，並有 Edge 或 Chrome。

## 主系統打包與部署

主系統打包包為本機產生的：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工閱讀器\InspectionReporting-manager.zip
```

主系統測試結果：`24 passed, 1 skipped`。

部署新版主系統時：

1. 關閉伺服器上的舊 `InspectionReporting.exe`。
2. 將新版 ZIP 解壓縮到新資料夾。
3. 備份伺服器舊資料夾的 `instance`。
4. 將舊版正式 `instance` 內容帶到新版資料夾。
5. 從新版資料夾執行 `InspectionReporting.exe`。
6. 測試 `http://192.168.0.193:5000/manager`。

不要用 ZIP 裡的空白／測試 `instance` 覆蓋正式 SQLite，也不要只複製 EXE。這是 onedir 發布包。

## 修 bug 時的判斷順序

### 若問題是 404 `/manager`

優先檢查：

1. 伺服器是否仍在跑舊版 `InspectionReporting.exe`
2. 新版 package 是否真的部署到 `192.168.0.193`
3. 新 package 是否包含 `templates\manager.html`
4. 是否使用正確網址：`/manager`

不要先修改 `ReportViewer.exe`。

### 若問題是閱讀器打不開或開錯網址

檢查：

1. 經理電腦的 `ReportViewer.json`
2. `ReportViewer.json` 是否和 `ReportViewer.exe` 在同一資料夾
3. Edge／Chrome 是否存在
4. 經理電腦是否能用瀏覽器開啟 `http://192.168.0.193:5000/manager`

### 若問題是資料或畫面內容錯誤

優先修改主系統：

- `app/routes.py`
- `app/services.py`
- `templates/manager.html`

測試時不要直接使用伺服器正式 SQLite，應使用測試設定與暫存資料庫。

## 新視窗建議開場訊息

可以直接貼上：

> 請先閱讀 `C:\Users\TJR_\Documents\ChatGPT\校機報工\docs\handoff-report-viewer-2026-10-28.md`，再檢查目前 Git 狀態。我現在要修的 bug 是：＿＿＿＿。請先判斷問題屬於主系統 `/manager`、主系統打包部署，還是 `inspection-report-viewer` 啟動器；不要先修改正式資料庫，也不要改動與 bug 無關的功能。

