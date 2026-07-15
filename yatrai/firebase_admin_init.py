"""
YatrAI — Firebase Admin SDK Initializer
----------------------------------------
Initializes the firebase-admin app ONCE (singleton pattern).

Credential resolution order:
  1. Local dev: reads ./serviceAccountKey.json if the file exists
  2. Render / production: reads FIREBASE_SERVICE_ACCOUNT_JSON env var
     (paste the JSON as a single-line string in Render's dashboard)
  3. If neither is found, admin SDK is NOT initialized and Firestore
     caching will gracefully fall back to in-memory caching.

Usage (anywhere in the backend):
    from yatrai.firebase_admin_init import get_firestore_client
    db = get_firestore_client()   # returns None if not initialized
"""

import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level singletons
_firebase_app = None
_firestore_client = None
_init_attempted = False  # guard: only try once even if it fails


def _initialize_firebase() -> bool:
    """
    Attempt to initialize firebase-admin. Returns True on success.
    Called lazily on first call to get_firestore_client().
    """
    global _firebase_app, _firestore_client, _init_attempted
    _init_attempted = True

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        # ── Already initialized by a previous call ──────────────────
        if firebase_admin._apps:
            _firebase_app = firebase_admin.get_app()
            _firestore_client = firestore.client()
            return True

        cred = None

        # ── Path 1: local serviceAccountKey.json ─────────────────────
        local_key_path = Path(__file__).resolve().parent.parent / "serviceAccountKey.json"
        if local_key_path.exists():
            cred = credentials.Certificate(str(local_key_path))
            logger.info("[Firebase] Using local serviceAccountKey.json")

        # ── Path 2: FIREBASE_SERVICE_ACCOUNT_JSON env var ────────────
        elif os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"):
            sa_json = os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"]
            sa_dict = json.loads(sa_json)
            cred = credentials.Certificate(sa_dict)
            logger.info("[Firebase] Using FIREBASE_SERVICE_ACCOUNT_JSON env var")

        else:
            logger.warning(
                "[Firebase] No credentials found — "
                "serviceAccountKey.json missing and FIREBASE_SERVICE_ACCOUNT_JSON not set. "
                "Firestore caching disabled; falling back to in-memory cache."
            )
            return False

        # Initialize the app with Firestore DB URL
        _firebase_app = firebase_admin.initialize_app(cred)
        _firestore_client = firestore.client()
        logger.info("[Firebase] Admin SDK initialized successfully (project: %s)",
                    _firebase_app.project_id)
        return True

    except Exception as e:
        logger.error("[Firebase] Initialization failed: %s — Firestore caching disabled.", e)
        return False


def get_firestore_client():
    """
    Returns the Firestore client, initializing firebase-admin on first call.
    Returns None if initialization failed or credentials are not available.
    """
    global _init_attempted, _firestore_client

    # Only attempt initialization once per process lifetime
    if not _init_attempted:
        _initialize_firebase()

    return _firestore_client
