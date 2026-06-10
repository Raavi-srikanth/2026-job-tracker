import os
import sys
import json
import httpx
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# 🎯 NEW TARGET ROLE KEYWORDS (Combines graduation batches, fresh roles, and early-career titles)
KEYWORDS = [
    "2026", "graduate", "trainee", "associate", "fresher", "entry level", 
    "ai engineer", "ml engineer", "machine learning", "artificial intelligence",
    "data scientist", "data analyst", "sde i", "sde-1", "software engineer i", 
    "software engineer-1", "intern", "internship"
]

# 📍 STRICT GEOGRAPHIC FILTER (Only alerts if the job is physically in these three tech hubs)
TARGET_LOCATIONS = ["bengaluru", "bangalore", "hyderabad", "chennai"]

# 🚫 EXCLUSION BLOCKLIST (Instantly drops senior profiles containing target words)
EXCLUSION_BLOCKLIST = ["senior", "sr.", "lead", "principal", "manager", "director", "architect", "years experience", "experience required"]

DB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def send_telegram_alert(company, title, url, location):
    """Drops a custom dashboard alert right into your private Telegram channel."""
    message = (
        f"🎯 *NEW 2026 / INTERN ROLE DETECTED*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 *Company:* {company}\n"
        f"💼 *Role:* {title}\n"
        f"📍 *Location:* {location}\n"
        f"🔗 [Apply Directly Here]({url})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ _Scanned: {datetime.now().strftime('%Y-%m-%d %H:%M')} IST_"
    )
    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        httpx.post(telegram_url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

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
        
        # 1. Antiduplication & Seniority Safeguard (Drop experienced profiles instantly)
        if any(block in title_lower for block in EXCLUSION_BLOCKLIST):
            continue
            
        # 2. Match Target Role or Target Batch
        if any(kw in title_lower for kw in KEYWORDS):
            # 3. Restrict Strictly to Bengaluru, Hyderabad, and Chennai
            if any(loc in location_lower for loc in TARGET_LOCATIONS):
                if not is_already_processed(job_id):
                    print(f"🔥 Validating Target Profile: {title} at {company_name} ({location})")
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
    # Pre-filtered payload at the country level for India
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
