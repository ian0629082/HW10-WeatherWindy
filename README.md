# CWA 即時天氣視覺化儀表板

本專案使用中央氣象署 CWA 開放資料集 `O-A0001-001`，抓取自動氣象站即時觀測資料，整理成 CSV 與 SQLite，並提供 FastAPI API 與網頁 Dashboard。

## Live Demo

<https://hw-10-weather-windy.vercel.app/>

## 專案功能

- 抓取 CWA 自動氣象站即時觀測資料
- 儲存為 `weather_observation.csv`
- 匯入 SQLite 資料庫 `weather.db`
- 提供 FastAPI 後端 API
- 提供網頁 Dashboard
- 支援縣市篩選
- 顯示測站地圖、氣溫、濕度、雨量、風速與降雨率估計

## 安裝套件

```powershell
pip install -r backend\requirements.txt
```

## 設定 CWA 授權碼

請在專案根目錄建立 `.env`，並放入 CWA API 授權碼：

```env
Authorization=your_token_here
```

也支援以下兩種寫法：

```env
CWA_AUTHORIZATION=your_token_here
CWA_API_KEY=your_token_here
```

`.env` 已加入 `.gitignore`，不會被提交到 GitHub。

## 抓取資料並建立資料庫

```powershell
python .\fetch_weather.py
```

執行後會產生：

- `weather_observation.csv`
- `weather.db`

SQLite 資料表：

- `stations`
- `observations`
- `forecasts`
- `predictions`

快速測試少量資料：

```powershell
python .\fetch_weather.py --limit 5
```

## 啟動本機 API 與 Dashboard

```powershell
python -m uvicorn backend.app.main:app --reload
```

開啟：

- Dashboard: <http://127.0.0.1:8000>
- API 文件: <http://127.0.0.1:8000/docs>

## API 端點

- `GET /api/health`
- `GET /api/stations`
- `GET /api/stations/{station_id}/latest`
- `GET /api/stations/{station_id}/observations`
- `GET /api/map/stations/latest`
- `GET /api/summary`
- `GET /api/stations/{station_id}/predictions`

## Vercel 部署

本專案已包含 Vercel 部署設定：

- `api/index.py`
- `vercel.json`
- `requirements.txt`

請在 Vercel 專案設定中加入環境變數：

```env
CWA_API_KEY=your_token_here
```

Vercel 環境不使用本機的 `weather.db`。部署後，API 會直接從 CWA 即時抓取資料，並在記憶體中短暫快取。

## Dashboard 顯示內容

- 縣市篩選器
- 測站地圖
- 測站 popup 即時觀測資料
- 測站數量
- 平均氣溫
- 平均濕度
- 累積雨量
- 平均風速
- 平均降雨率
- 縣市測站排行圖表

## 降雨率說明

目前的降雨率是透明的 baseline 估計，模型名稱為：

```text
heuristic-baseline-v1
```

它會根據以下欄位估算：

- 目前雨量
- 天氣文字
- 相對濕度
- 氣壓
- 風速

這還不是正式訓練完成的機器學習模型，因為目前資料庫只有單一觀測時間點。若之後累積多個時間點的歷史資料，可以再改成 Random Forest、XGBoost 或時間序列模型。

## 注意事項

- 不要提交 `.env`
- 不要提交 CWA API Key
- `weather.db`、CSV、log、原始 JSON 都已被 `.gitignore` 排除
- 若要重新部署 Vercel，確認 Vercel Environment Variables 已設定 `CWA_API_KEY`
