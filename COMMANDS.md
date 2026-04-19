# Commands

## Repository folder
cd C:\Users\davew\repos\trade-pilot

## Backend (FastAPI)

Activate the virtual environment first:
```
venv\Scripts\activate
```

Run the API server:
```
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

Run with auto-reload (development):
```
uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
```

Run the scheduler (separate process):
```
python main.py
```

Run a specific job manually:
```
python main.py --job pre_market
python main.py --job market_open --dry-run
python main.py --job market_open --symbol AAPL
```

Run both API + scheduler together (as in production):
```
bash start.sh
```

---

## Frontend (React / Vite)

```
cd frontend && npm run dev
```

Build for production:
```
npm run build
```

Preview production build:
```
npm run preview
```
