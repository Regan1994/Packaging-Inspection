# Packaging-Inspection｜新專案交接文件

更新日期：2026-11-03

本文件供後續新對話接續「校機＋包裝」整合新專案使用。

## 1. Repository 與基準

- GitHub：`https://github.com/Regan1994/Packaging-Inspection.git`
- 本機目錄：`C:\Users\TJR_\Documents\ChatGPT\Packaging-Inspection`
- 目前分支：`main`
- 目前基準 commit：`0f36d76 Fix manager Enter key to search`
- 基準來源：原校機報工專案 `Regan1994/inspection-reporting-barcode` 的 V701.11.03 已提交程式碼。
- 已推送至新 repository 的 `origin/main`。

新 repository 已複製原始碼、模板、靜態檔、測試與既有文件，共 77 個追蹤檔案。

以下資料**沒有**帶入新 repository：

- `instance/`
- SQLite 資料庫與備份
- `dist/`、`build/`
- 虛擬環境與 pytest 暫存檔
- 原專案未追蹤的交接文件

## 2. 舊校機專案保留原狀

原校機專案：

```text
C:\Users\TJR_\Documents\ChatGPT\校機報工
```

規則：

1. 後續校機＋包裝整合開發只在 `Packaging-Inspection` 進行。
2. 不修改、覆蓋、提交或重新打包原校機專案。
3. 不修改原校機專案的 `instance/inspection.db`、`instance/backups/` 或正式資料。
4. 原校機專案保留為可啟動的備援系統。
5. 新專案未來使用自己的 `instance/inspection.db`；不可與原系統共用同一個活躍 SQLite 檔案。

若未來需要既有校機資料，應先建立原資料庫備份，再以一次性的複製／migration 導入新專案資料庫；不可讓兩套程式同時使用同一個 SQLite 檔案。

## 3. 目標架構

新專案是未來中央主機運行的主線：

```text
中央主機
├─ /
│  └─ 系統首頁
├─ /inspection
│  └─ 校機系統
└─ /packing
   └─ 包裝系統
```

- 校機與包裝由同一台中央主機提供網頁服務。
- 各部門電腦以瀏覽器連線，不在每台電腦安裝完整程式。
- 校機與包裝共用訂單／產品識別資料，但各自保存製程紀錄。
- 包裝不建立成完全獨立的新程式。
- 長期可再逐步延伸組裝、出貨與完整產品履歷。

目前基準程式已可在區域網路監聽 `0.0.0.0:5000`，但尚未實作中央首頁、`/inspection` 前綴或包裝模組。

## 4. 已確認的包裝規則

### 包裝狀態

包裝狀態只使用三種顯示值：

- 未開始
- 包裝中
- 包裝完成

不新增「待校機」、「不可開始」或其他包裝狀態／說明欄。

校機尚未完成時，包裝狀態仍可顯示「未開始」，但後端必須拒絕開始包裝。

### 前置條件與防重複

- 校機完成後，產品才可以開始包裝。
- 同一筆訂單／產品不得重複開始包裝。
- 同一筆訂單／產品不得重複完成包裝。
- 包裝開始與完成必須由同一位包裝員工執行。

### 條碼與掃碼流程

不新增包裝條碼、產品條碼或訂單條碼。

包裝沿用既有同一筆訂單／產品的校機 START 與 FINISH 條碼：

```text
包裝員工條碼
→ 既有訂單／產品 START 條碼
→ 進行包裝
→ 同一位包裝員工條碼
→ 同一筆既有訂單／產品 FINISH 條碼
```

包裝模組須把既有 START／FINISH 條碼解析至同一筆訂單／產品，不建立任何包裝專用條碼。

## 5. 員工資料方向

校機與包裝人員不共用，因為部門人員不會重複。

因此建議新增獨立資料：

```text
inspection_worker  現有校機人員
packing_worker     新增包裝人員
```

`packing_worker` 預計包含：

- 員工編號
- 姓名
- 員工條碼
- 啟用狀態

包裝人員的 START 與 FINISH 必須對應到同一筆 `packing_worker`。

## 6. 包裝資料方向

包裝資料不可塞進 `inspection_order` 的校機欄位。

建議新增：

```text
packing_record
- id
- inspection_order_id       # 指向既有校機訂單；應唯一
- packing_worker_id
- worker_no_snapshot
- worker_name_snapshot
- status                    # pending / started / completed
- started_at
- finished_at
- created_at
- updated_at

packing_scan_event
packing_audit_log
```

畫面將狀態轉成：

```text
pending   → 未開始
started   → 包裝中
completed → 包裝完成
```

`packing_record.inspection_order_id` 必須具唯一限制，並以資料庫交易／條件更新保護 START、FINISH 的重複掃描與多人同時操作。

## 7. 校機管理畫面預期變更

校機管理／閱讀器畫面需要保留現有校機資料，並新增包裝資訊：

- 包裝狀態
- 包裝人員
- 包裝開始時間
- 包裝完成時間

包裝狀態僅顯示「未開始／包裝中／包裝完成」，不另加說明欄。

## 8. 尚未確認，實作前再討論

以下事項目前不要自行假設或實作：

1. Web 帳號與部門權限的具體登入方式。
   - 目前舊系統只有單一管理密碼，沒有完整角色帳號。
   - 已知方向：校機帳號主要使用校機、包裝帳號主要使用包裝、管理者查看全部。
2. 新專案正式上線時，是否要導入既有校機歷史資料，以及導入的時間點。
3. 中央主機的 IP／主機名稱、Windows 自動啟動、防火牆與外部備份位置。
4. 是否在第一版就將既有根路徑改為 `/inspection`，以及舊書籤的轉址策略。
5. 組裝與售服系統的實際整合範圍；目前不應合併其資料庫。

## 9. 新對話開場文字

```text
請先閱讀：
C:\Users\TJR_\Documents\ChatGPT\Packaging-Inspection\docs\NEW_PROJECT_HANDOFF_2026-11-03.md

這是校機＋包裝的新主線專案，repository 是 Regan1994/Packaging-Inspection，main 目前以原校機 V701.11.03 commit 0f36d76 為基準。

原校機專案 C:\Users\TJR_\Documents\ChatGPT\校機報工 必須保持不修改，作為備援；不共用原專案的 SQLite／instance。

請先檢查 Git 狀態與本文件。開始實作前先整理方案並等待我確認。
```
