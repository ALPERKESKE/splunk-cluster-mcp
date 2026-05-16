"""Pytest fixtures and dotenv load."""
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root so smoke tests pick up cluster credentials
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
