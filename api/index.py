"""Vercel ASGI entry point for the Glitch FastAPI backend."""

import sys
from pathlib import Path

# Vercel Python builder runs api/index.py with cwd=api/, so we need to add
# the project root to sys.path for the `backend` package import to work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import app

__all__ = ["app"]
