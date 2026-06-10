import os
import sys
import json
import httpx
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Keywords targeted to trap early-career 2026 graduate tracking profiles
KEYWORDS = ["2026", "graduate", "trainee", "associate", "entry level", "sde-1", "off-campus", "campus", "fresher"]

# STRICT INDIA LOCALITY ALLOW-LIST
INDIA_LOCATIONS = ["india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "noida", "gurgaon", "gurugram", "chennai"]

# HARD BANNED FOREIGN WORDS
GLOBAL_BLOCKLIST = ["mexico", "francisco", "london", "singapore", "tokyo", "berlin", "toronto", "dubai", "remote us", "usa", "united states"]

DB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def send_telegram_alert(company, title, url, location):
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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try: httpx.post(telegram_url, json=payload, timeout=10)
    except Exception as e: print(f"Telegram error: {e}")

def is_already_processed(job_id):
    url = f"{SUPABASE_URL}/rest/v1/processed_jobs?job_id=eq.{job_id}&select=job_id"
    try:
        response = httpx.get(url, headers=DB_HEADERS)
        return len(response.json()) > 0
    except: return False

def save_job_to_db(job_id, company, title, job_url):
    url = f"{SUPABASE_URL}/rest/v1/processed_jobs"
    payload = {"job_id": str(job_id), "company_name": company, "title": title, "url": job_url}
    try: httpx.post(url, json=payload, headers=DB_HEADERS)
    except: pass

def process_matches(found_jobs, company_name):
    for job in found_jobs:
        title = job['title']
        title_lower = title.lower()
        job_id = job['id']
        job_url = job['url']
        location = job.get('location', 'India')
        location_lower = location.lower()
        
        # 1. Keyword validation check
        if any(kw in title_lower for kw in KEYWORDS):
            # 2. Strict Blocklist Check (Instant drop if global city matches)
            if any(block in location_lower for block in GLOBAL_BLOCKLIST) or any(block in title_lower for block in GLOBAL_BLOCKLIST):
                continue
                
            # 3. India Allowlist Validation Check
            if any(loc in location_lower for loc in INDIA_LOCATIONS):
                if not is_already_processed(job_id):
                    save_job_to_db(job_id, company_name, title, job_url)
                    send_telegram_alert(company_name, title, job_url, location)

def fetch_greenhouse(subdomain, company_name):
    url = f"https://boards-api.greenhouse.io/v1/boards/{subdomain}/jobs"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{"id": f"gh-{j['id']}", "title": j['title'], "url": j['absolute_url'], "location": j.get('location', {}).get('name', 'India')} for j in res.get('jobs', [])]
        process_matches(jobs, company_name)
    except: pass

def fetch_lever(token, company_name):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{"id": f"lev-{j['id']}", "title": j['text'], "url": j['hostedUrl'], "location": j.get('categories', {}).get('location', 'India')} for j in res]
        process_matches(jobs, company_name)
    except: pass

def fetch_workday(subdomain, company_name):
    url = f"https://{subdomain}.myworkdayjobs.com/wday/cxs/{subdomain}/Careers/jobs"
    payload = {"appliedFacets": {"locationCountry": ["bc33aa3152ec42d4995f4791a106ed09"]}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        res = httpx.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15).json()
        jobs = [{"id": f"wd-{j['jobPostingId']}", "title": j['title'], "url": f"https://{subdomain}.myworkdayjobs.com" + j['externalPath'], "location": j.get('locationsText', 'India')} for j in res.get('jobPostings', [])]
        process_matches(jobs, company_name)
    except: pass

if __name__ == "__main__":
    db_url = f"{SUPABASE_URL}/rest/v1/tracking_companies?is_active=eq.true"
    try:
        companies = httpx.get(db_url, headers=DB_HEADERS).json()
        for target in companies:
            if target['ats_type'] == 'workday': fetch_workday(target['token_or_subdomain'], target['company_name'])
            elif target['ats_type'] == 'greenhouse': fetch_greenhouse(target['token_or_subdomain'], target['company_name'])
            elif target['ats_type'] == 'lever': fetch_lever(target['token_or_subdomain'], target['company_name'])
    except Exception as e: print(f"DB Error: {e}")
