from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import aiosqlite
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_PASSWORD = "admin123"
DB_PATH = os.path.join(BASE_DIR, "bookings.db")
TIME_SLOTS = ["17:00", "18:00", "19:00", "20:00", "21:00", "22:00", "23:00", "00:00", "01:00", "02:00"]
FIELDS = [1, 2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                field_number INTEGER NOT NULL,
                date TEXT NOT NULL,
                time_slot TEXT NOT NULL,
                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(field_number, date, time_slot)
            )
        """)
        await db.commit()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "change-me-random-key"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/health")
async def health():
    return {"status": "ok"}


async def get_bookings(date_str: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bookings WHERE date = ?", (date_str,)) as cursor:
            rows = await cursor.fetchall()
    bookings = {}
    for row in rows:
        key = f"{row['field_number']}_{row['time_slot']}"
        bookings[key] = {"id": row["id"], "client_name": row["client_name"], "client_phone": row["client_phone"]}
    return bookings


def date_context(date_str: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return {
        "date": date_str,
        "today": today,
        "prev_date": (d - timedelta(days=1)).strftime("%Y-%m-%d"),
        "next_date": (d + timedelta(days=1)).strftime("%Y-%m-%d"),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, date: str = None):
    date = date or datetime.now().strftime("%Y-%m-%d")
    bookings = await get_bookings(date)
    ctx = date_context(date)
    return templates.TemplateResponse("index.html", {
        "request": request, **ctx, "bookings": bookings,
        "time_slots": TIME_SLOTS, "fields": FIELDS,
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, date: str = None):
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=302)
    date = date or datetime.now().strftime("%Y-%m-%d")
    bookings = await get_bookings(date)
    ctx = date_context(date)
    return templates.TemplateResponse("admin.html", {
        "request": request, **ctx, "bookings": bookings,
        "time_slots": TIME_SLOTS, "fields": FIELDS,
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form()):
    if password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный пароль"})


@app.get("/logout")
async def logout(request: Request):
    request.session.pop("is_admin", None)
    return RedirectResponse("/", status_code=302)


@app.post("/api/book")
async def book(request: Request):
    if not request.session.get("is_admin"):
        return JSONResponse({"error": "Нет доступа"}, status_code=403)
    data = await request.json()
    field, date, time_slot = data.get("field"), data.get("date"), data.get("time_slot")
    name, phone = data.get("name", "").strip(), data.get("phone", "").strip()
    if not all([field, date, time_slot, name, phone]):
        return JSONResponse({"error": "Заполните все поля"}, status_code=400)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO bookings (field_number, date, time_slot, client_name, client_phone) VALUES (?, ?, ?, ?, ?)",
                (field, date, time_slot, name, phone),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            return JSONResponse({"error": "Этот слот уже занят"}, status_code=409)
    return JSONResponse({"ok": True})


@app.post("/api/cancel")
async def cancel(request: Request):
    if not request.session.get("is_admin"):
        return JSONResponse({"error": "Нет доступа"}, status_code=403)
    data = await request.json()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bookings WHERE id = ?", (data.get("id"),))
        await db.commit()
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
