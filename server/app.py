"""
The telemetry service: one endpoint that receives pings, a few that read
aggregates, and the dashboard that draws them.

	POST /v1/ping                          what clients send, once a day
	GET  /v1/projects                      one card per project
	GET  /v1/projects/{id}/summary?days=   one project's dashboard
	GET  /v1/projects/{id}/manifest        what a project collects
	GET  /                                 the dashboard
	GET  /{id}, /{id}/privacy

Configured by environment:

	DATA_DIR       where telemetry.db lives                (default ./data)
	PROJECTS_DIR   where the manifests are                 (default ./projects)
	WEB_DIR        the static dashboard                    (default ./web)
	RATE_LIMIT     pings per IP and hour                   (default 30)
	CACHE_SECONDS  how long an aggregate is reused         (default 600)

Run with `uvicorn --factory app:create_app`.
"""

import json
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import manifest as manifests_module
import rollup
import summary
import validate
from db import Database

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", os.path.join(ROOT, "projects"))
WEB_DIR = os.environ.get("WEB_DIR", os.path.join(ROOT, "web"))
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "30"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "600"))

# A day's counters for every button of a big project fit in a few KB.
MAX_BODY_BYTES = 32 * 1024
LANGUAGES = ("es", "en")


class RateLimiter:
	"""
	Pings per IP over the last hour, kept in memory only.

	Generous on purpose: several installations behind one home connection is
	normal. This is here to stop a loop, not to count people.
	"""

	def __init__(self, limit, window=3600):
		self.limit = limit
		self.window = window
		self._hits = defaultdict(deque)
		self._lock = threading.Lock()

	def allow(self, key, now=None):
		now = now if now is not None else time.monotonic()
		with self._lock:
			hits = self._hits[key]
			while hits and hits[0] <= now - self.window:
				hits.popleft()
			if len(hits) >= self.limit:
				return False
			hits.append(now)
			# Forget addresses that went quiet, or the table only grows.
			if len(self._hits) > 10_000:
				for stale in [ip for ip, queue in self._hits.items() if not queue]:
					del self._hits[stale]
			return True


def _language(request, requested):
	if requested in LANGUAGES:
		return requested
	accepted = request.headers.get("accept-language", "").lower()
	return "es" if accepted.startswith(("es", "ca", "gl")) else "en"


def create_app(data_dir=DATA_DIR, projects_dir=PROJECTS_DIR, web_dir=WEB_DIR,
			rate_limit=RATE_LIMIT, cache_seconds=CACHE_SECONDS):
	os.makedirs(data_dir, exist_ok=True)
	manifests = manifests_module.load_all(projects_dir)
	database = Database(os.path.join(data_dir, "telemetry.db"))
	limiter = RateLimiter(rate_limit)
	cache = {}
	cache_lock = threading.Lock()

	def cached(key, build):
		now = time.monotonic()
		with cache_lock:
			hit = cache.get(key)
			if hit and hit[0] > now:
				return hit[1]
		value = build()
		with cache_lock:
			cache[key] = (now + cache_seconds, value)
		return value

	def housekeeping_forever():
		# Totals first, then the rows they were made from: the other way round
		# a month could lose its rows before it was ever counted.
		while True:
			try:
				rollup.run(database, manifests)
				database.prune()
			except Exception as e:
				print(f"housekeeping failed: {e}", flush=True)
			time.sleep(86400)

	@asynccontextmanager
	async def lifespan(app):
		threading.Thread(target=housekeeping_forever, daemon=True).start()
		yield
		database.close()

	app = FastAPI(title="telemetry", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
	app.state.db = database
	app.state.manifests = manifests

	@app.post("/v1/ping")
	async def ping(request: Request):
		ip = request.client.host if request.client else "unknown"
		if not limiter.allow(ip):
			return JSONResponse({"error": "rate limited"}, status_code=429)

		raw = await request.body()
		if len(raw) > MAX_BODY_BYTES:
			return JSONResponse({"error": "too large"}, status_code=413)
		try:
			body = json.loads(raw)
		except ValueError:
			return JSONResponse({"error": "not JSON"}, status_code=400)
		try:
			project, clean = validate.ping(body, manifests)
		except validate.Rejected as e:
			return JSONResponse({"error": str(e)}, status_code=400)

		answer = {"enabled": project["enabled"], "next_ping_h": project["interval_hours"]}
		# A project switched off is told so, and what it sent is not kept.
		if project["enabled"]:
			await run_in_threadpool(database.record, clean)
		return answer

	@app.get("/v1/projects")
	def projects(request: Request, lang: str = None):
		language = _language(request, lang)
		return cached(("overview", language), lambda: summary.overview(database, manifests, language))

	@app.get("/v1/projects/{project_id}/summary")
	def project_summary(project_id: str, request: Request, days: int = Query(30), lang: str = None):
		manifest = manifests.get(project_id)
		if manifest is None:
			raise HTTPException(404, "unknown project")
		if days not in summary.RANGES:
			raise HTTPException(400, f"days must be one of {summary.RANGES}")
		language = _language(request, lang)
		return cached((project_id, days, language),
					lambda: summary.project(database, manifest, days, language))

	@app.get("/v1/projects/{project_id}/manifest")
	def project_manifest(project_id: str):
		manifest = manifests.get(project_id)
		if manifest is None:
			raise HTTPException(404, "unknown project")
		return manifests_module.public(manifest)

	@app.get("/healthz")
	def health():
		return {"ok": True, "projects": len(manifests)}

	app.mount("/static", StaticFiles(directory=web_dir), name="static")

	def page(name):
		return FileResponse(os.path.join(web_dir, name), headers={"Cache-Control": "no-cache"})

	@app.get("/")
	def index():
		return page("index.html")

	@app.get("/{project_id}")
	def project_page(project_id: str):
		if project_id not in manifests:
			raise HTTPException(404, "unknown project")
		return page("project.html")

	@app.get("/{project_id}/privacy")
	def privacy_page(project_id: str):
		if project_id not in manifests:
			raise HTTPException(404, "unknown project")
		return page("privacy.html")

	return app

