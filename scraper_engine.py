import os
import sys
import json
import httpx
import re
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# 🎯 TECH ROLE CORE KEYWORDS
TECH_ROLES = ["ai", "ml", "machine learning", "artificial intelligence", "data scientist", "data analyst", "sde", "software engineer", "software developer"]

# ⏱️ EARLY CAREER MARKERS
EXPERIENCE_MARKERS = ["2026", "graduate", "trainee", "fresher", "entry level", "intern", "internship", "i", "-1", " 1", "university"]

# 📍 STRICT GEOGRAPHIC HUB FILTER
TARGET_LOCATIONS = ["bengaluru", "bangalore", "hyderabad", "chennai"]

# 🚫 HARD TITLE EXCLUSION BLOCKLIST 
EXCLUSION_BLOCKLIST = [
    "senior", "sr.", "lead", "principal", "manager", "director", "architect", "staff", "mts",
    "auditor", "audit", "content", "copy writer", "graphic", "designer", "video", "editor", 
    "hr", "talent acquisition", "recruiter", "benefits", "operations", "people", "marketing", "martech",
    "ii", "iii"
]

# ⛔ REGEX PATTERNS TO DETECT EXPERIENCE IN JOB DESCRIPTIONS
# This catches things like: "2+ years", "3-5 years", "requires 4 years", "minimum of 2 years experience"
EXPERIENCE_REGEX = [
    r"([1-9]|\d+)\s*\+?\s*years?", 
    r"([1-9])\s*-\s*(\d+)\s*years?",
    r"experience\s*required"
]

DB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def check_jd_experience(ats_type, job_id, token_or_subdomain):
    """Deep-scans the raw job description text from the source API to enforce 0 years of experience."""
    text_to_scan = ""
    
    try:
        if ats_type == "greenhouse":
            # Greenhouse internal content API
            clean_id = job_id.replace("gh-", "")
            url = f"https://boards-api.greenhouse.io/v1/boards/{token_or_subdomain}/jobs/{clean_id}"
            res = httpx.get(url, timeout=10).json()
            text_to_scan = res.get("content", "").lower()
            
        elif ats_type == "lever":
            # Lever internal content API
            clean_id = job_id.replace("lev-", "")
            url = f"https://api.lever.co/v0/postings/{token_or_subdomain}/{clean_id}"
            res = httpx.get(url, timeout=10).json()
            text_to_scan = (res.get("description", "") + " " + res.get("lists", {}).get("requirements", "")).lower()
            
        elif ats_type == "workday":
            # Workday internal content detailed API
            url = f"https://{token_or_subdomain}.myworkdayjobs.com/wday/cxs/{token_or_subdomain}/Careers/jobDetails"
            # Workday needs the direct external path string back to the server
            payload = {"externalPath": job_id}
            res = httpx.post(url, json=payload, headers={"Accept": "application/json"}, timeout=10).json()
            text_to_scan = res.get("jobDescription", "").lower()
    except Exception as e:
        print(f"Failed to fetch JD description for deep scan {job_id}: {e}")
        return True # Safer to skip if the description can't be fetched

    # Look for explicit experience markers (like 2+ years, 3 years, etc.)
    for pattern in EXPERIENCE_REGEX:
        matches = re.findall(pattern, text_to_scan)
        for match in matches:
            # If a tuple is returned by regex, extract the numbers safely
            years = int(match[0]) if isinstance(match, tuple) else int(match) if match.isdigit() else 99
            # Allow 0 or 1 year configurations as fallback buffers for entry level tracks
            if years >= 2:
                print(f"🚫 Deep Scan Dropped {job_id}: Found required experience phrase match '{years} years' in description text.")
                return False

    return True

def send_telegram_alert(company, title, url, location):
    message = (
        f"🎯 *NEW 2026 / INTERN TECH ROLE DETECTED*\n"
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

def process_matches(found_jobs, company_name, ats_type, token_or_subdomain):
    for job in found_jobs:
        title = job['title']
        title_lower = title.lower()
        job_id = job['id']
        job_url = job['url']
        location = job.get('location', 'India')
        location_lower = location.lower()
        
        # 1. Broad Title Exclusions Check
        if any(block in title_lower for block in EXCLUSION_BLOCKLIST):
            continue
            
        # 2. Strict City Boundary Check
        if any(loc in location_lower for loc in TARGET_LOCATIONS):
            
            has_tech_core = any(tech in title_lower for tech in TECH_ROLES)
            has_early_marker = any(marker in title_lower for marker in EXPERIENCE_MARKERS)
            
            if has_tech_core or has_early_marker:
                if not is_already_processed(job_id):
                    
                    # 3. DEEP SCANDING STEP: Open up the JD backend structure and audit for hidden experience requirements
                    # For Workday, we need to pass the raw path string down to the post request payload
                    wday_raw_path = job.get('raw_path', job_id)
                    scan_target_id = wday_raw_path if ats_type == 'workday' else job_id
                    
                    if not check_jd_experience(ats_type, scan_target_id, token_or_subdomain):
                        # Blocked by the deep description scanner logic loop!
                        continue
                        
                    print(f"🔥 Validated Target Profile: {title} at {company_name} ({location})")
                    save_job_to_db(job_id, company_name, title, job_url)
                    send_telegram_alert(company_name, title, job_url, location)

def fetch_greenhouse(subdomain, company_name):
    url = f"https://boards-api.greenhouse.io/v1/boards/{subdomain}/jobs"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{"id": f"gh-{j['id']}", "title": j['title'], "url": j['absolute_url'], "location": j.get('location', {}).get('name', 'India')} for j in res.get('jobs', [])]
        process_matches(jobs, company_name, "greenhouse", subdomain)
    except: pass

def fetch_lever(token, company_name):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{"id": f"lev-{j['id']}", "title": j['text'], "url": j['hostedUrl'], "location": j.get('categories', {}).get('location', 'India')} for j in res]
        process_matches(jobs, company_name, "lever", token)
    except: pass

def fetch_workday(subdomain, company_name):
    url = f"https://{subdomain}.myworkdayjobs.com/wday/cxs/{subdomain}/Careers/jobs"
    payload = {"appliedFacets": {"locationCountry": ["bc33aa3152ec42d4995f4791a106ed09"]}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        res = httpx.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15).json()
        jobs = [{
            "id": f"wd-{j['jobPostingId']}",
            "raw_path": j['externalPath'],
            "title": j['title'],
            "url": f"https://{subdomain}.myworkdayjobs.com" + j['externalPath'],
            "location": j.get('locationsText', 'India')
        } for j in res.get('jobPostings', [])]
        process_matches(jobs, company_name, "workday", subdomain)
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
