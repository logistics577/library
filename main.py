import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from jose import jwt, JWTError
from passlib.context import CryptContext
from pymongo import MongoClient

# ------------------------------------------------------------------
# Config (hardcoded — edit these values directly)
# ------------------------------------------------------------------
MONGO_URI = "mongodb+srv://zapierobroy_db_user:k6RbBfQHjc_535XsdFERG@cluster0.ncgeqd2.mongodb.net/?appName=Cluster0"
MONGO_DB_NAME = "library_tracker"

JWT_SECRET = "12345"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72

AUTH_EMAIL = "library77777@yopmail.com"
AUTH_PASSWORD_PLAIN = "library@12345"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
AUTH_PASSWORD_HASH = pwd_context.hash(AUTH_PASSWORD_PLAIN)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
topics_col = db["library_topics"]
subtopics_col = db["library_subtopics"]

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
# Mongo helpers
# ------------------------------------------------------------------
def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")


def serialize(doc: dict) -> dict:
    if doc is None:
        return None
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


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
    topics = list(topics_col.find().sort("created_at", -1))
    subtopics = list(subtopics_col.find().sort("created_at", 1))

    by_topic = {}
    for s in subtopics:
        by_topic.setdefault(str(s["topic_id"]), []).append(serialize(s))

    result = []
    for t in topics:
        t_ser = serialize(t)
        t_ser["subtopics"] = by_topic.get(t_ser["id"], [])
        result.append(t_ser)
    return result


@app.post("/api/topics", status_code=201)
def create_topic(body: TopicIn, user: str = Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    topic_row = {
        "title": body.title,
        "notes": body.notes or "",
        "status": body.status or "pending",
        "target_date": body.target_date,
        "target_time": body.target_time,
        "completed_at": now if body.status == "completed" else None,
        "created_at": now,
    }
    inserted_id = topics_col.insert_one(topic_row).inserted_id
    topic = serialize(topics_col.find_one({"_id": inserted_id}))

    created_subs = []
    for sub in (body.subtopics or []):
        if not sub.title.strip():
            continue
        sub_now = datetime.now(timezone.utc).isoformat()
        sub_row = {
            "topic_id": str(inserted_id),
            "title": sub.title,
            "notes": sub.notes or "",
            "status": sub.status or "pending",
            "target_date": sub.target_date,
            "target_time": sub.target_time,
            "completed_at": sub_now if sub.status == "completed" else None,
            "created_at": sub_now,
        }
        sub_id = subtopics_col.insert_one(sub_row).inserted_id
        created_subs.append(serialize(subtopics_col.find_one({"_id": sub_id})))

    topic["subtopics"] = created_subs
    return topic


@app.put("/api/topics/{topic_id}")
def update_topic(topic_id: str, body: TopicUpdate, user: str = Depends(get_current_user)):
    updates = {k: v for k, v in body.dict(exclude_unset=True).items() if k != "status"}
    updates.update(status_fields(body.status))
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = topics_col.find_one_and_update(
        {"_id": oid(topic_id)}, {"$set": updates}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Topic not found")
    return serialize(result)


@app.delete("/api/topics/{topic_id}", status_code=204)
def delete_topic(topic_id: str, user: str = Depends(get_current_user)):
    topics_col.delete_one({"_id": oid(topic_id)})
    subtopics_col.delete_many({"topic_id": topic_id})
    return None


# ------------------------------------------------------------------
# Subtopic routes
# ------------------------------------------------------------------
@app.post("/api/topics/{topic_id}/subtopics", status_code=201)
def add_subtopic(topic_id: str, body: SubtopicIn, user: str = Depends(get_current_user)):
    topic = topics_col.find_one({"_id": oid(topic_id)})
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    now = datetime.now(timezone.utc).isoformat()
    sub_row = {
        "topic_id": topic_id,
        "title": body.title,
        "notes": body.notes or "",
        "status": body.status or "pending",
        "target_date": body.target_date,
        "target_time": body.target_time,
        "completed_at": now if body.status == "completed" else None,
        "created_at": now,
    }
    sub_id = subtopics_col.insert_one(sub_row).inserted_id
    return serialize(subtopics_col.find_one({"_id": sub_id}))


@app.put("/api/subtopics/{subtopic_id}")
def update_subtopic(subtopic_id: str, body: SubtopicUpdate, user: str = Depends(get_current_user)):
    updates = {k: v for k, v in body.dict(exclude_unset=True).items() if k != "status"}
    updates.update(status_fields(body.status))
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = subtopics_col.find_one_and_update(
        {"_id": oid(subtopic_id)}, {"$set": updates}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Subtopic not found")
    return serialize(result)


@app.delete("/api/subtopics/{subtopic_id}", status_code=204)
def delete_subtopic(subtopic_id: str, user: str = Depends(get_current_user)):
    subtopics_col.delete_one({"_id": oid(subtopic_id)})
    return None


# ------------------------------------------------------------------
# Dashboard stats
# ------------------------------------------------------------------
@app.get("/api/stats")
def get_stats(user: str = Depends(get_current_user)):
    topics = list(topics_col.find())
    subtopics = list(subtopics_col.find())

    topic_ids_with_subs = {s["topic_id"] for s in subtopics}
    standalone_topics = [t for t in topics if str(t["_id"]) not in topic_ids_with_subs]

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
