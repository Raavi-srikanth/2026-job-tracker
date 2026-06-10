import os
import sys
import json
import httpx
from datetime import datetime

# Fetch securely stored cloud secrets
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Keywords targeted to trap early-career 2026 graduate tracking profiles
KEYWORDS = ["2026", "graduate", "trainee", "associate", "entry level", "sde-1", "off-campus", "campus", "fresher"]

# Standalone configuration header set for Supabase REST engine integration
DB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def send_telegram_alert(company, title, url, location):
    """Fires a clean, beautifully formatted markdown notification straight to your Telegram channel."""
    message = (
        f"🚨 *NEW OFF-CAMPUS ROLE DETECTED (2026 Batch)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 *Company:* {company}\n"
        f"💼 *Role:* {title}\n"
        f"📍 *Location:* {location if location else 'India'}\n"
        f"🔗 [Apply Directly Here]({url})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ _Scanned: {datetime.now().strftime('%Y-%m-%d %H:%M')} IST_"
    )
    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown", 
        "disable_web_page_preview": True
    }
    try:
        httpx.post(telegram_url, json=payload, timeout=10)
    except Exception as e:
        print(f"Failed to alert Telegram channel: {e}")

def is_already_processed(job_id):
    """Queries Supabase database to verify if this specific job ID string was already parsed."""
    url = f"{SUPABASE_URL}/rest/v1/processed_jobs?job_id=eq.{job_id}&select=job_id"
    try:
        response = httpx.get(url, headers=DB_HEADERS)
        return len(response.json()) > 0
    except Exception as e:
        print(f"Database duplicate check error: {e}")
        return False

def save_job_to_db(job_id, company, title, job_url):
    """Inserts a freshly discovered job ID into Supabase to prevent duplicate pings."""
    url = f"{SUPABASE_URL}/rest/v1/processed_jobs"
    payload = {"job_id": str(job_id), "company_name": company, "title": title, "url": job_url}
    try:
        httpx.post(url, json=payload, headers=DB_HEADERS)
    except Exception as e:
        print(f"Database insertion write failed: {e}")

def process_matches(found_jobs, company_name):
    """Scans clean title strings for 2026/early career parameters and handles routing logs."""
    for job in found_jobs:
        title = job['title']
        title_lower = title.lower()
        job_id = job['id']
        job_url = job['url']
        location = job.get('location', 'India')
        
        # Parse exact keyword combinations matching early-career roles
        if any(kw in title_lower for kw in KEYWORDS):
            if not is_already_processed(job_id):
                print(f"🔥 Found NEW matching profile: '{title}' at {company_name}")
                save_job_to_db(job_id, company_name, title, job_url)
                send_telegram_alert(company_name, title, job_url, location)

# ─── ATS STRUCTURAL API INTERCEPTORS ───

def fetch_greenhouse(subdomain, company_name):
    url = f"https://boards-api.greenhouse.io/v1/boards/{subdomain}/jobs"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{
            "id": f"gh-{j['id']}",
            "title": j['title'],
            "url": j['absolute_url'],
            "location": j.get('location', {}).get('name', 'India')
        } for j in res.get('jobs', [])]
        process_matches(jobs, company_name)
    except Exception:
        pass

def fetch_lever(token, company_name):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{
            "id": f"lev-{j['id']}",
            "title": j['text'],
            "url": j['hostedUrl'],
            "location": j.get('categories', {}).get('location', 'India')
        } for j in res]
        process_matches(jobs, company_name)
    except Exception:
        pass

def fetch_workday(subdomain, company_name):
    url = f"https://{subdomain}.myworkdayjobs.com/wday/cxs/{subdomain}/Careers/jobs"
    payload = {
        "appliedFacets": {"locationCountry": ["bc33aa3152ec42d4995f4791a106ed09"]}, 
        "limit": 20, 
        "offset": 0, 
        "searchText": ""
    }
    try:
        res = httpx.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15).json()
        jobs = [{
            "id": f"wd-{j['jobPostingId']}",
            "title": j['title'],
            "url": f"https://{subdomain}.myworkdayjobs.com" + j['externalPath'],
            "location": j.get('locationsText', 'India')
        } for j in res.get('jobPostings', [])]
        process_matches(jobs, company_name)
    except Exception:
        pass

# ─── RUN ENGINE LOOP ───

def main():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("❌ Script Error: Missing environment config secrets variables.")
        sys.exit(1)
        
    db_url = f"{SUPABASE_URL}/rest/v1/tracking_companies?is_active=eq.true"
    try:
        companies = httpx.get(db_url, headers=DB_HEADERS).json()
        print(f"🚀 Scanner fully awake. Checking {len(companies)} enterprise multinational portals...")
        
        for target in companies:
            name = target['company_name']
            ats = target['ats_type'].lower()
            token = target['token_or_subdomain']
            
            if ats == 'workday':
                fetch_workday(token, name)
            elif ats == 'greenhouse':
                fetch_greenhouse(token, name)
            elif ats == 'lever':
                fetch_lever(token, name)
                
        print("🏁 Scan processing cycle successfully finished.")
    except Exception as e:
        print(f"Critical operational error running script loop: {e}")

if __name__ == "__main__":
    main()
