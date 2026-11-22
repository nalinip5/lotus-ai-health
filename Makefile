.PHONY: install dev api web build test run clean

install:            ## install backend + frontend dependencies
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

api:                ## run the API with auto-reload on :8000
	cd backend && uvicorn lotus.main:app --reload --port 8000

web:                ## run the console dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

build:              ## build the console into frontend/dist (served by the API)
	cd frontend && npm run build

run: build          ## single process: API + built console on :8000
	cd backend && uvicorn lotus.main:app --port 8000

test:
	cd backend && python -m pytest -q

clean:              ## wipe local data (cases, checkpoints, blobs)
	rm -rf backend/.lotus-data
