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

# 🎯 CORE TECH ROLE KEYWORDS
TECH_ROLES = ["ai", "ml", "machine learning", "artificial intelligence", "data scientist", "data analyst", "sde", "software engineer", "software developer", "programmer", "tech intern", "software engr"]

# ⏱️ TECH EARLY CAREER MARKERS
EXPERIENCE_MARKERS = ["2026", "graduate", "trainee", "fresher", "entry level", "intern", "internship", "i", "-1", " 1", "university"]

# 📍 STRICT GEOGRAPHIC HUB FILTER
TARGET_LOCATIONS = ["bengaluru", "bangalore", "hyderabad", "chennai"]

# 🚫 HARD EXCLUSION BLOCKLIST 
EXCLUSION_BLOCKLIST = [
    "senior", "sr.", "lead", "principal", "manager", "director", "architect", "staff", "mts", "ii", "iii",
    "auditor", "audit", "content", "copy writer", "graphic", "designer", "video", "editor", 
    "hr", "talent acquisition", "recruiter", "benefits", "operations", "people", "marketing", "martech",
    "sales", "executive", "account", "alliances", "brand", "customer success", "cst", "support", "business development", "bde", "finance"
]

# ⛔ REGEX PATTERNS TO DETECT EXPERIENCE IN JOB DESCRIPTIONS
EXPERIENCE_REGEX = [
    r"([2-9]|\d+)\s*\+?\s*years?", 
    r"([2-9])\s*-\s*(\d+)\s*years?",
    r"experience\s*required"
]

DB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def check_jd_experience(ats_type, job_id, token_or_subdomain):
    """Deep-scans raw job description texts from Oracle, Greenhouse, Lever, and Workday."""
    text_to_scan = ""
    try:
        if ats_type == "oracle":
            # Oracle Cloud Candidate Experience API endpoint
            url = f"https://{token_or_subdomain}.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCandidateExperienceJobPostings/{job_id}"
            res = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
            text_to_scan = (res.get("Title", "") + " " + res.get("ShortDescription", "") + " " + res.get("LongDescription", "")).lower()
        elif ats_type == "greenhouse":
            clean_id = job_id.replace("gh-", "")
            url = f"https://boards-api.greenhouse.io/v1/boards/{token_or_subdomain}/jobs/{clean_id}"
            res = httpx.get(url, timeout=10).json()
            text_to_scan = res.get("content", "").lower()
        elif ats_type == "lever":
            clean_id = job_id.replace("lev-", "")
            url = f"https://api.lever.co/v0/postings/{token_or_subdomain}/{clean_id}"
            res = httpx.get(url, timeout=10).json()
            text_to_scan = (res.get("description", "") + " " + res.get("lists", {}).get("requirements", "")).lower()
        elif ats_type == "workday":
            url = f"https://{token_or_subdomain}.myworkdayjobs.com/wday/cxs/{token_or_subdomain}/Careers/jobDetails"
            payload = {"externalPath": job_id}
            res = httpx.post(url, json=payload, headers={"Accept": "application/json"}, timeout=10).json()
            text_to_scan = res.get("jobDescription", "").lower()
    except Exception as e:
        print(f"Failed to fetch JD description for {job_id}: {e}")
        return True 

    for pattern in EXPERIENCE_REGEX:
        if re.search(pattern, text_to_scan):
            print(f"🚫 Deep Scan Dropped {job_id}: Found required experience phrase match in description text.")
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
    try: return len(httpx.get(url, headers=DB_HEADERS).json()) > 0
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
        
        if any(block in title_lower for block in EXCLUSION_BLOCKLIST):
            continue
            
        if any(loc in location_lower for loc in TARGET_LOCATIONS):
            has_tech_core = any(tech in title_lower for tech in TECH_ROLES)
            has_early_marker = any(marker in title_lower for marker in EXPERIENCE_MARKERS)
            
            if has_tech_core and has_early_marker:
                if not is_already_processed(job_id):
                    wday_raw_path = job.get('raw_path', job_id)
                    scan_target_id = wday_raw_path if ats_type == 'workday' else job_id
                    
                    if not check_jd_experience(ats_type, scan_target_id, token_or_subdomain):
                        continue
                        
                    print(f"🔥 Validated Target Profile: {title} at {company_name} ({location})")
                    save_job_to_db(job_id, company_name, title, job_url)
                    send_telegram_alert(company_name, title, job_url, location)

def fetch_oracle(subdomain, company_name):
    """Hits the direct internal JSON endpoints running behind corporate Oracle Cloud installations."""
    url = f"https://{subdomain}.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCandidateExperienceJobPostings?limit=25&locationId=300000002344073" # Targets India geography filters dynamically
    try:
        res = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()
        jobs = []
        for item in res.get('items', []):
            locations_list = [loc.get('Name', '') for loc in item.get('JobLocations', [])]
            locations_str = ", ".join(locations_list) if locations_list else "India"
            jobs.append({
                "id": f"or-{item['Id']}",
                "title": item['Title'],
                "url": f"https://{subdomain}.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Honeywell/job/{item['Id']}",
                "location": locations_str
            })
        process_matches(jobs, company_name, "oracle", subdomain)
    except Exception as e: print(f"Oracle fetch failed for {company_name}: {e}")

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
            elif target['ats_type'] == 'oracle': fetch_oracle(target['token_or_subdomain'], target['company_name'])
    except Exception as e: print(f"DB Error: {e}")
