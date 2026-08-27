

import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from jose import jwt, JWTError
from passlib.context import CryptContext
from supabase import create_client, Client

load_dotenv()
# ------------------------------------------------------------------
# Config (override all of these with real environment variables)
# ------------------------------------------------------------------
SUPABASE_URL = "https://obnhesobzgppiidigdtu.supabase.co"
SUPABASE_KEY ="sb_publishable_-zpPTE45VhRROAZOV0xxFg_iTMVSYLA"

JWT_SECRET =  "12345"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int("72")

AUTH_EMAIL = "library77777@yopmail.com"
# Plaintext fallback is only used to auto-hash on first run if no hash is set.
AUTH_PASSWORD_PLAIN ="library@12345"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH") or pwd_context.hash(AUTH_PASSWORD_PLAIN)

if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_KEY is not set. Export your Supabase service_role key as "
        "the SUPABASE_KEY environment variable before starting the server."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------------------------------------------
# App setup
# ------------------------------------------------------------------
app = FastAPI(title="Library Study Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer_scheme = HTTPBearer()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.html")


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


class SubtopicIn(BaseModel):
    title: str
    notes: Optional[str] = ""
    status: Optional[str] = "pending"
    target_date: Optional[str] = None
    target_time: Optional[str] = None


class SubtopicUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    target_date: Optional[str] = None
    target_time: Optional[str] = None


class TopicIn(BaseModel):
    title: str
    notes: Optional[str] = ""
    status: Optional[str] = "pending"
    target_date: Optional[str] = None
    target_time: Optional[str] = None
    subtopics: Optional[List[SubtopicIn]] = Field(default_factory=list)


class TopicUpdate(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    target_date: Optional[str] = None
    target_time: Optional[str] = None


# ------------------------------------------------------------------
# Auth helpers
# ------------------------------------------------------------------
def create_access_token(email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": email, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    token = creds.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
        if email != AUTH_EMAIL:
            raise HTTPException(status_code=401, detail="Invalid session")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


# ------------------------------------------------------------------
# Status helper: keep completed_at in sync with status
# ------------------------------------------------------------------
def status_fields(new_status: Optional[str]) -> dict:
    if new_status is None:
        return {}
    if new_status not in ("pending", "completed"):
        raise HTTPException(status_code=400, detail="status must be 'pending' or 'completed'")
    fields = {"status": new_status}
    fields["completed_at"] = datetime.now(timezone.utc).isoformat() if new_status == "completed" else None
    return fields


# ------------------------------------------------------------------
# Auth routes
# ------------------------------------------------------------------
@app.post("/api/login")
def login(body: LoginRequest):
    if body.email.strip().lower() != AUTH_EMAIL.lower():
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not pwd_context.verify(body.password, AUTH_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(AUTH_EMAIL)
    return {"access_token": token, "token_type": "bearer", "email": AUTH_EMAIL}


# ------------------------------------------------------------------
# Topic routes
# ------------------------------------------------------------------
@app.get("/api/topics")
def list_topics(user: str = Depends(get_current_user)):
    topics = supabase.table("library_topics").select("*").order("created_at", desc=True).execute().data
    subtopics = supabase.table("library_subtopics").select("*").order("created_at", desc=False).execute().data

    by_topic = {}
    for s in subtopics:
        by_topic.setdefault(s["topic_id"], []).append(s)

    result = []
    for t in topics:
        subs = by_topic.get(t["id"], [])
        result.append({**t, "subtopics": subs})
    return result


@app.post("/api/topics", status_code=201)
def create_topic(body: TopicIn, user: str = Depends(get_current_user)):
    topic_row = {
        "title": body.title,
        "notes": body.notes or "",
        "status": body.status or "pending",
        "target_date": body.target_date,
        "target_time": body.target_time,
        "completed_at": datetime.now(timezone.utc).isoformat() if body.status == "completed" else None,
    }
    inserted = supabase.table("library_topics").insert(topic_row).execute().data
    if not inserted:
        raise HTTPException(status_code=500, detail="Could not create topic")
    topic = inserted[0]

    created_subs = []
    for sub in (body.subtopics or []):
        if not sub.title.strip():
            continue
        sub_row = {
            "topic_id": topic["id"],
            "title": sub.title,
            "notes": sub.notes or "",
            "status": sub.status or "pending",
            "target_date": sub.target_date,
            "target_time": sub.target_time,
            "completed_at": datetime.now(timezone.utc).isoformat() if sub.status == "completed" else None,
        }
        res = supabase.table("library_subtopics").insert(sub_row).execute().data
        if res:
            created_subs.append(res[0])

    topic["subtopics"] = created_subs
    return topic


@app.put("/api/topics/{topic_id}")
def update_topic(topic_id: str, body: TopicUpdate, user: str = Depends(get_current_user)):
    updates = {k: v for k, v in body.dict(exclude_unset=True).items() if k != "status"}
    updates.update(status_fields(body.status))
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = supabase.table("library_topics").update(updates).eq("id", topic_id).execute().data
    if not res:
        raise HTTPException(status_code=404, detail="Topic not found")
    return res[0]


@app.delete("/api/topics/{topic_id}", status_code=204)
def delete_topic(topic_id: str, user: str = Depends(get_current_user)):
    supabase.table("library_topics").delete().eq("id", topic_id).execute()
    return None


# ------------------------------------------------------------------
# Subtopic routes
# ------------------------------------------------------------------
@app.post("/api/topics/{topic_id}/subtopics", status_code=201)
def add_subtopic(topic_id: str, body: SubtopicIn, user: str = Depends(get_current_user)):
    topic = supabase.table("library_topics").select("id").eq("id", topic_id).execute().data
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    sub_row = {
        "topic_id": topic_id,
        "title": body.title,
        "notes": body.notes or "",
        "status": body.status or "pending",
        "target_date": body.target_date,
        "target_time": body.target_time,
        "completed_at": datetime.now(timezone.utc).isoformat() if body.status == "completed" else None,
    }
    res = supabase.table("library_subtopics").insert(sub_row).execute().data
    if not res:
        raise HTTPException(status_code=500, detail="Could not create subtopic")
    return res[0]


@app.put("/api/subtopics/{subtopic_id}")
def update_subtopic(subtopic_id: str, body: SubtopicUpdate, user: str = Depends(get_current_user)):
    updates = {k: v for k, v in body.dict(exclude_unset=True).items() if k != "status"}
    updates.update(status_fields(body.status))
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = supabase.table("library_subtopics").update(updates).eq("id", subtopic_id).execute().data
    if not res:
        raise HTTPException(status_code=404, detail="Subtopic not found")
    return res[0]


@app.delete("/api/subtopics/{subtopic_id}", status_code=204)
def delete_subtopic(subtopic_id: str, user: str = Depends(get_current_user)):
    supabase.table("library_subtopics").delete().eq("id", subtopic_id).execute()
    return None


# ------------------------------------------------------------------
# Dashboard stats
# ------------------------------------------------------------------
@app.get("/api/stats")
def get_stats(user: str = Depends(get_current_user)):
    topics = supabase.table("library_topics").select("*").execute().data
    subtopics = supabase.table("library_subtopics").select("*").execute().data

    # Trackable units = topics that have no subtopics (tracked at topic level)
    # plus every subtopic (tracked individually).
    topic_ids_with_subs = {s["topic_id"] for s in subtopics}
    standalone_topics = [t for t in topics if t["id"] not in topic_ids_with_subs]

    units = standalone_topics + subtopics
    total = len(units)
    completed = len([u for u in units if u["status"] == "completed"])
    pending = total - completed
    percent = round((completed / total) * 100, 1) if total else 0.0

    days_map = {}
    for u in units:
        if u["status"] == "completed" and u.get("completed_at"):
            day = u["completed_at"][:10]
            days_map[day] = days_map.get(day, 0) + 1
    completed_by_day = [{"date": d, "count": c} for d, c in sorted(days_map.items())]

    return {
        "total_topics": len(topics),
        "total_units": total,
        "completed": completed,
        "pending": pending,
        "percent_completed": percent,
        "completed_by_day": completed_by_day,
    }


# ------------------------------------------------------------------
# Frontend — served directly, no /static or /templates folder
# ------------------------------------------------------------------
@app.get("/")
def serve_index():
    return FileResponse(INDEX_FILE)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}
