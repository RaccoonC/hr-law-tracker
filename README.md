# 人事法規追蹤儀表板

自動追蹤人事相關法規異動與新聞草案動態的靜態網頁。每天由 GitHub Actions 排程抓取，
無需自己的伺服器，全部跑在 GitHub 免費額度內。

## 這個工具做什麼、不做什麼

**會做：**
- 每天檢查設定檔中列出的法規，比對「全國法規資料庫」上的修正日期是否有變化
- 每天用關鍵字搜尋 Google 新聞，抓出可能相關的修法新聞、草案動態
- 網頁上把「近期有更新」的法規標記出來，方便你一眼看到要去查什麼

**不會做：**
- 不會重製法規全文（法規內容一律連回官方網站查看，避免版本、時效性問題）
- 不保證抓到「即將施行」的所有細節，新聞只是提醒，正式依據仍需自行查證官方公告
- 全國法規資料庫本身每週五才整批更新，所以就算每天檢查，實際看到變化的頻率大約是每週一次

---

## 部署步驟（第一次設定）

### 1. 建立 GitHub Repository
1. 到 GitHub 建立一個新的 repository（public 或 private 皆可，private 也能用免費額度的 GitHub Pages 需 Pro 方案；若要用免費 Pages，建議設為 **public**）
2. 把這個資料夾的所有檔案上傳／push 上去

```bash
cd hr-law-tracker
git init
git add .
git commit -m "init: 人事法規追蹤儀表板"
git branch -M main
git remote add origin https://github.com/你的帳號/你的repo名稱.git
git push -u origin main
```

### 2. 開啟 Actions 的寫入權限
GitHub Actions 預設只能讀取 repo，但這個工具需要把抓到的資料自動 commit 回去，所以：
1. 到 repo 的 **Settings → Actions → General**
2. 找到「Workflow permissions」
3. 選擇 **「Read and write permissions」**
4. 儲存

### 3. 手動觸發第一次執行
1. 到 repo 的 **Actions** 分頁
2. 選擇「Daily HR Law Check」這個工作流程
3. 點右邊的「Run workflow」手動跑一次，不用等到明天
4. 執行完成後，`data/laws_status.json` 和 `data/news_feed.json` 應該會被自動更新並 commit

### 4. 開啟 GitHub Pages
1. 到 repo 的 **Settings → Pages**
2. Source 選擇 **Deploy from a branch**
3. Branch 選 `main`，資料夾選 `/ (root)`
4. 儲存後，等 1-2 分鐘，網頁就會出現在 `https://你的帳號.github.io/你的repo名稱/`

---

## 如何新增要追蹤的法規

1. 到 [全國法規資料庫](https://law.moj.gov.tw/) 搜尋法規名稱
2. 開啟該法規頁面後，網址列會長得像：
   `https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=N0030001`
3. `PCode=` 後面那串（例如 `N0030001`）就是你要填入 `config/laws.json` 的 `pcode`
4. 在 `config/laws.json` 加一筆：

```json
{
  "category": "你想分的類別（例如：勞動條件）",
  "name": "法規正式名稱",
  "pcode": "剛剛複製的代碼",
  "note": ""
}
```

5. commit、push 上去，下次排程執行時就會一併追蹤

已經先幫你填好且驗證過的：勞動基準法、性別平等工作法、職業安全衛生法、就業服務法、
勞工保險條例、就業保險法。其餘（勞基法施行細則、勞工請假規則、外籍勞工相關子法、
勞工退休金條例等）留空，需要你自己補上 PCode，因為這類子法規、辦法的代碼變動較頻繁，
現查現填比較準確。

## 如何調整新聞追蹤關鍵字

編輯 `config/keywords.json`，每一行一個查詢關鍵字（會直接丟進 Google 新聞搜尋），
可以依你實際遇到的狀況增減，例如加入「大量解僱」「基本工資 2027」等。

---

## 檔案結構

```
hr-law-tracker/
├── .github/workflows/daily-check.yml   # 每日排程設定
├── config/
│   ├── laws.json       # 要追蹤的法規清單
│   └── keywords.json   # 新聞搜尋關鍵字
├── scripts/
│   ├── check_laws.py   # 抓法規修正日期、比對異動
│   └── check_news.py   # 抓新聞 RSS、標記新出現的項目
├── data/
│   ├── laws_status.json  # 產出：法規目前狀態（勿手動編輯，會被覆蓋）
│   └── news_feed.json    # 產出：新聞清單（勿手動編輯，會被覆蓋）
└── index.html           # 儀表板網頁本體
```

## 已知限制 / 未來如果想擴充可以考慮的方向

- **法規抓取方式**：目前用官方頁面的「列印精簡版」擷取修正日期的文字，屬於輕量、
  低頻率的存取方式，但官方頁面若改版，擷取邏輯（`scripts/check_laws.py` 裡的正規表達式）
  可能需要跟著調整。如果之後想要更穩固的做法，可以改用全國法規資料庫的
  [Open API](https://law.moj.gov.tw/api/)（整批下載 JSON/XML 再自行過濾），
  只是資料量較大，需要額外處理。
- **新聞來源單一**：目前只用 Google 新聞 RSS。如果想涵蓋更多，可以之後加入
  勞動部新聞稿、立法院法律系統議案進度等來源，做法類似，用同樣的比對邏輯即可。
- **通知**：目前只在網頁上顯示標記，你選擇的是「自己每天看網頁」。如果之後想加 Email
  通知，可以在 workflow 裡加一段「有新的 updated 項目時發信」的邏輯（例如串接
  SendGrid 或用 GitHub Actions 的 email action），需要另外申請 API 金鑰。
