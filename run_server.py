#!/usr/bin/env python3
"""
DueClock Server Launcher
Starts the DueClock statutory engine, API services, and Buyer Portal on port 8000.
"""

import uvicorn
import sys

def main():
    print("=" * 65)
    print(" ⏱️  DueClock: Statutory-Clock & Section 43B(h) Autopilot OS")
    print("=" * 65)
    print(" • Cloud API & Core:        http://127.0.0.1:8000")
    print(" • API Docs (Swagger):      http://127.0.0.1:8000/docs")
    print(" • Buyer No-Login Portal:   http://127.0.0.1:8000/p/inv_critical_overdue")
    print(" • Executive Master UI:     file:///Users/vkm/Desktop/DueClock/index.html")
    print("=" * 65)
    
    uvicorn.run("dueclock.app:app", host="127.0.0.1", port=8000, reload=True)

if __name__ == "__main__":
    main()
