from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, status, Request, Form
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import SQLModel, Session, select, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from datetime import datetime
import os
import aiofiles
import json
import secrets
import hmac
import hashlib
import httpx
from typing import Optional, List

from database.session import get_session, create_db_and_tables
from models.user import User, UserCreate, UserResponse
from models.document import Document, DocumentCreate, DocumentUpdate
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_admin,
    get_current_manager,
)
from services.weather import get_weather

# ==========================================
# WEBHOOK MODEL (Exercise 3)
# ==========================================

class Webhook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str
    event_type: str  # e.g., "document.enriched", "document.uploaded"
    secret: str       # Signature key for HMAC validation
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


app = FastAPI(title="SendIt API C027-01-213/2023", version="1.0.0")

# Setup configuration
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", 5 * 1024 * 1024))  # 5 MB
ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".docx"]

# Rate Limiter setup
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


async def dispatch_webhook(event_type: str, payload: dict, session: Session):
    """Dispatches webhooks asynchronously with an HMAC SHA-256 signature."""
    webhooks = session.exec(
        select(Webhook).where(Webhook.event_type == event_type, Webhook.is_active == True)
    ).all()
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        for hook in webhooks:
            signature = hmac.new(
                hook.secret.encode(),
                json.dumps(payload).encode(),
                hashlib.sha256
            ).hexdigest()
            
            headers = {
                "Content-Type": "application/json",
                "X-SendIt-Signature": signature
            }
            try:
                await client.post(hook.url, json=payload, headers=headers)
            except Exception as e:
                print(f"Failed to deliver webhook to {hook.url}: {e}")



@app.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, session: Session = Depends(get_session)):
    existing_user = session.exec(select(User).where(User.username == user_in.username)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    existing_email = session.exec(select(User).where(User.email == user_in.email)).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    db_user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        full_name=user_in.full_name,
        role=user_in.role
    )
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user


@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user account")
    
    user.last_login = datetime.utcnow()
    session.commit()
    
    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/users/me", response_model=UserResponse)
def get_user_profile(current_user: User = Depends(get_current_user)):
    """Retrieve details of the currently authenticated user."""
    return current_user



@app.post("/documents/upload")
@limiter.limit("10/hour")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    city: str = Form(...),
    description: Optional[str] = Form(None),
    country: str = Form("Kenya"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Upload a document with validation and automated versioning.
    Enriches with weather data for the specified city.
    """
    # 1. Validate file extension
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # 2. Read and validate file size
    contents = await file.read()
    file_size = len(contents)
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max size: {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )
    
    # 3. Document Versioning (Exercise 2)
    existing_doc = session.exec(
        select(Document)
        .where(Document.original_filename == file.filename, Document.uploader_id == current_user.id)
        .order_by(Document.version.desc())
    ).first()
    
    new_version = (existing_doc.version + 1) if (existing_doc and hasattr(existing_doc, 'version')) else 1

    # 4. Generate safe unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_filename = f"{timestamp}_v{new_version}_{current_user.id}_{file.filename.replace(' ', '_')}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    # 5. Save file asynchronously
    async with aiofiles.open(file_path, "wb") as out_file:
        await out_file.write(contents)
    
    # 6. Create document database record
    document = Document(
        filename=safe_filename,
        original_filename=file.filename,
        version=new_version,
        file_size=file_size,
        file_type=file.content_type or "application/octet-stream",
        city=city,
        country=country,
        description=description,
        uploader_id=current_user.id,
        file_path=file_path,
        status="processing"
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    
    # 7. Enrich with weather data (External API Call)
    try:
        weather_data = await get_weather(city, country)
        if weather_data and "error" not in weather_data:
            document.weather_data = json.dumps(weather_data)
            document.weather_fetched_at = datetime.utcnow()
            document.status = "enriched"
            session.commit()
            
            # Webhook Notification (Exercise 3)
            await dispatch_webhook(
                "document.enriched", 
                {"document_id": document.id, "filename": document.original_filename, "status": "enriched"}, 
                session
            )
        else:
            document.status = "uploaded"
            session.commit()
    except Exception as e:
        print(f"Weather API error during upload: {e}")
        document.status = "uploaded"
        session.commit()
    
    return {
        "message": "Document uploaded successfully",
        "document_id": document.id,
        "filename": document.original_filename,
        "version": document.version,
        "status": document.status
    }


@app.get("/documents")
@limiter.limit("30/minute")
def list_documents(
    request: Request,
    status: Optional[str] = None,
    city: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """List all documents with optional filters."""
    query = select(Document)
    
    # Managers and admins see all documents; staff see only their own
    if current_user.role not in ["admin", "manager"]:
        query = query.where(Document.uploader_id == current_user.id)
    
    if status:
        query = query.where(Document.status == status)
    if city:
        query = query.where(Document.city == city)
    
    return session.exec(query).all()


@app.get("/documents/search")
@limiter.limit("20/minute")
def search_documents(
    request: Request,
    q: Optional[str] = None,
    city: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Search and filter documents with role-based access isolation."""
    query = select(Document)
    
    if current_user.role not in ["admin", "manager"]:
        query = query.where(Document.uploader_id == current_user.id)
    
    if q:
        search_pattern = f"%{q}%"
        query = query.where(
            (Document.original_filename.ilike(search_pattern)) | 
            (Document.description.ilike(search_pattern))
        )
    
    if city:
        query = query.where(Document.city.ilike(city))
    if status:
        query = query.where(Document.status == status)
    if date_from:
        query = query.where(Document.uploaded_at >= date_from)
    if date_to:
        query = query.where(Document.uploaded_at <= date_to)
        
    return session.exec(query).all()


@app.get("/documents/{document_id}")
@limiter.limit("30/minute")
def get_document(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Get a specific document."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if current_user.role not in ["admin", "manager"] and document.uploader_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return document


@app.patch("/documents/{document_id}")
def update_document(
    document_id: int,
    doc_update: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Update document metadata (description, city, country)."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if current_user.role not in ["admin", "manager"] and document.uploader_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this document")
    
    update_data = doc_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(document, key, value)
        
    document.updated_at = datetime.utcnow()
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


@app.delete("/documents/{document_id}")
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_manager),
    session: Session = Depends(get_session)
):
    """Delete a document (managers and admins only)."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if os.path.exists(document.file_path):
        os.remove(document.file_path)
    
    session.delete(document)
    session.commit()
    return {"message": "Document deleted successfully"}



@app.post("/documents/{document_id}/enrich")
@limiter.limit("5/minute")
async def enrich_document(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_manager),
    session: Session = Depends(get_session)
):
    """Manually trigger weather enrichment for a document."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if document.status == "enriched":
        return {"message": "Document already enriched"}
    
    weather_data = await get_weather(document.city, document.country)
    if weather_data and "error" not in weather_data:
        document.weather_data = json.dumps(weather_data)
        document.weather_fetched_at = datetime.utcnow()
        document.status = "enriched"
        session.commit()
        
        # Dispatch webhook notification
        await dispatch_webhook(
            "document.enriched", 
            {"document_id": document.id, "filename": document.original_filename, "status": "enriched"}, 
            session
        )
        
        return {
            "message": "Document enriched successfully",
            "weather": weather_data
        }
    else:
        document.status = "failed"
        session.commit()
        raise HTTPException(status_code=500, detail="Failed to enrich document with weather data")


@app.post("/documents/enrich-all")
@limiter.limit("2/minute")
async def bulk_enrich_documents(
    request: Request,
    current_user: User = Depends(get_current_manager),
    session: Session = Depends(get_session)
):
    """Find all documents with status 'uploaded' or 'failed' and attempt re-enrichment."""
    query = select(Document).where(Document.status.in_(["uploaded", "failed"]))
    documents = session.exec(query).all()
    
    enriched_count = 0
    failed_count = 0
    
    for doc in documents:
        try:
            weather_data = await get_weather(doc.city, doc.country)
            if weather_data and "error" not in weather_data:
                doc.weather_data = json.dumps(weather_data)
                doc.weather_fetched_at = datetime.utcnow()
                doc.status = "enriched"
                enriched_count += 1
                
                await dispatch_webhook(
                    "document.enriched", 
                    {"document_id": doc.id, "filename": doc.original_filename, "status": "enriched"}, 
                    session
                )
            else:
                doc.status = "failed"
                failed_count += 1
        except Exception:
            doc.status = "failed"
            failed_count += 1
            
    session.commit()
    return {
        "message": "Bulk enrichment process completed",
        "total_processed": len(documents),
        "successfully_enriched": enriched_count,
        "failed": failed_count
    }


@app.get("/documents/{document_id}/weather")
@limiter.limit("10/minute")
def get_document_weather(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """Get the weather data associated with a document."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if current_user.role not in ["admin", "manager"] and document.uploader_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not document.weather_data:
        raise HTTPException(status_code=404, detail="No weather data available for this document")
    
    return {
        "document_id": document.id,
        "city": document.city,
        "country": document.country,
        "weather": json.loads(document.weather_data)
    }



@app.post("/webhooks/register")
def register_webhook(
    webhook_url: str,
    event_type: str,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """Register a webhook endpoint for automated event dispatches."""
    secret = secrets.token_hex(32)
    webhook = Webhook(url=webhook_url, event_type=event_type, secret=secret)
    session.add(webhook)
    session.commit()
    session.refresh(webhook)
    return {
        "message": "Webhook registered successfully", 
        "webhook_id": webhook.id, 
        "secret": secret
    }