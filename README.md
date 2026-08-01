# SendIt API

SendIt API is a FastAPI-based backend service designed for logistics and delivery document management. It features JWT role-based authentication, safe multi-file upload validation, document versioning, real-time external weather enrichment via Open-Meteo, HMAC-signed webhook notifications, rate limiting, advanced search, and PostgreSQL persistence with SQLModel.

---

## Key Features

- **Authentication & Security:** Secure password hashing using `bcrypt` and JWT token-based authentication.
- **Role-Based Access Control (RBAC):** Strict operational permissions defined for `admin`, `manager`, and `staff` roles.
- **Document Versioning & Storage:** Re-uploading a document with the same original filename increments its `version` tracking while storing each physical revision safely on disk using `aiofiles`.
- **Advanced Document Search:** Multi-filter querying supporting full-text keyword matching (`q`), target `city`, document `status`, and date ranges (`date_from`, `date_to`).
- **External Weather Integration:** Asynchronous geocoding and weather enrichment via `httpx` to attach metrics to delivery destinations.
- **Webhook Dispatch System:** Asynchronous notification engine that delivers HMAC-SHA256 signed payloads to registered webhook endpoints upon document events (e.g., `document.enriched`).
- **Bulk Operations:** Dedicated endpoint for managers to batch re-enrich pending or failed documents.
- **Rate Limiting:** Request throttling configured with `slowapi` to prevent API abuse.

---

## Tech Stack

- **Framework:** FastAPI
- **Database:** PostgreSQL 16 & SQLModel (SQLAlchemy ORM)
- **Async & HTTP:** `httpx`, `aiofiles`
- **Security:** `passlib[bcrypt]`, `python-jose`
- **Rate Limiting:** SlowAPI
- **Containerization:** Docker & Docker Compose

---

## Project Structure

```text
sendit-api/
├── database/
│   └── session.py       # Engine creation & session generator
├── models/
│   ├── user.py          # User SQLModel schemas
│   ├── document.py      # Document SQLModel schemas
│   └── webhook.py       # Webhook SQLModel schema
├── services/
│   └── weather.py       # Open-Meteo async geocoding & weather fetching
├── uploads/             # Physical storage directory for versioned uploads
├── auth.py              # Password hashing & JWT helpers
├── main.py              # Application setup, router endpoints, & webhooks
├── seeds.py             # Database seed script for initial roles/users
├── docker-compose.yml   # PostgreSQL container configuration
├── .env                 # Environment configurations
└── README.md            # System documentation