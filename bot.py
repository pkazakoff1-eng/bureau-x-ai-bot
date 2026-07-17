#!/usr/bin/env python3
"""BX Assistant — точка входа."""
from src.app import build_app

if __name__ == "__main__":
    app = build_app()
    print("BX Assistant v9 — modular")
    app.run_polling()
