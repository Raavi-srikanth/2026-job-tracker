import os
import sys
import json
import httpx
import re
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STALE_SCAN_HOURS = int(os.environ.get("STALE_SCAN_HOURS", "24"))

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

def send_telegram_message(message, markdown=True):
    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }
    if markdown:
        payload["parse_mode"] = "Markdown"
    try:
        httpx.post(telegram_url, json=payload, timeout=10)
    except Exception as e: print(f"Telegram error (len={len(message)}): {e}")

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
    send_telegram_message(message, markdown=True)

def is_already_processed(job_id):
    url = f"{SUPABASE_URL}/rest/v1/processed_jobs?job_id=eq.{job_id}&select=job_id"
    try: return len(httpx.get(url, headers=DB_HEADERS).json()) > 0
    except: return False

def save_job_to_db(job_id, company, title, job_url):
    url = f"{SUPABASE_URL}/rest/v1/processed_jobs"
    payload = {"job_id": str(job_id), "company_name": company, "title": title, "url": job_url}
    try: httpx.post(url, json=payload, headers=DB_HEADERS)
    except: pass

def log_scan_run(company_name, ats_type, jobs_fetched, new_jobs, success, error_message, started_at, finished_at):
    url = f"{SUPABASE_URL}/rest/v1/scan_runs"
    payload = {
        "company_name": company_name,
        "ats_type": ats_type,
        "jobs_fetched": jobs_fetched,
        "new_jobs": new_jobs,
        "success": success,
        "error_message": error_message,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat()
    }
    try:
        httpx.post(url, json=payload, headers=DB_HEADERS, timeout=10)
    except Exception as e:
        log_error = f"Scan run logging failed for {company_name} (POST {url}): {e}"
        print(log_error)
        send_telegram_message(f"⚠️ {log_error}", markdown=False)

def parse_iso_datetime(value):
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def check_stale_scans(companies, stale_hours):
    stale_companies = []
    now_utc = datetime.now(timezone.utc)
    for company in companies:
        query = {
            "company_name": f"eq.{company['company_name']}",
            "success": "eq.true",
            "select": "finished_at",
            "order": "finished_at.desc",
            "limit": "1"
        }
        try:
            response = httpx.get(f"{SUPABASE_URL}/rest/v1/scan_runs", headers=DB_HEADERS, params=query, timeout=10).json()
            if not response:
                stale_companies.append(f"{company['company_name']} (no successful scan)")
                continue
            latest_scan = parse_iso_datetime(response[0].get("finished_at"))
            if latest_scan is None:
                stale_companies.append(f"{company['company_name']} (invalid finished_at format)")
            elif (now_utc - latest_scan) > timedelta(hours=stale_hours):
                stale_companies.append(f"{company['company_name']} (stale)")
        except Exception as e:
            stale_companies.append(f"{company['company_name']} (check failed: {type(e).__name__}: {e})")
    if stale_companies:
        stale_lines = "\n".join([f"- {entry}" for entry in stale_companies])
        send_telegram_message(
            f"⚠️ *Stale Scan Alert*\n"
            f"The following active companies have no successful scan in the last {stale_hours} hours:\n"
            f"{stale_lines}",
            markdown=True
        )

def send_run_summary(total_companies, successful_scans, total_jobs, new_jobs, failed_companies):
    failed_count = len(failed_companies)
    failure_lines = "\n".join([f"- {item}" for item in failed_companies]) if failed_companies else "- None"
    summary = (
        f"📊 *Scan Run Summary*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 Companies scanned: {total_companies}\n"
        f"✅ Successful scans: {successful_scans}\n"
        f"📄 Jobs fetched: {total_jobs}\n"
        f"🆕 New jobs matched: {new_jobs}\n"
        f"❌ Failed scans: {failed_count}\n"
        f"Failures:\n{failure_lines}"
    )
    send_telegram_message(summary, markdown=True)
    if successful_scans == 0:
        send_telegram_message(
            f"🚨 *Heartbeat Alert*\n"
            f"No company scans succeeded in this run.\n"
            f"Attempts: {total_companies}, Failures: {failed_count}\n"
            f"Please investigate workflow/runtime health immediately.",
            markdown=True
        )

def process_matches(found_jobs, company_name, ats_type, token_or_subdomain):
    new_jobs = 0
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
                    new_jobs += 1
    return new_jobs

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
        new_jobs = process_matches(jobs, company_name, "oracle", subdomain)
        return {"success": True, "jobs_fetched": len(jobs), "new_jobs": new_jobs, "error": None}
    except Exception as e:
        error = str(e)
        print(f"Oracle fetch failed for {company_name}: {error}")
        return {"success": False, "jobs_fetched": 0, "new_jobs": 0, "error": error}

def fetch_greenhouse(subdomain, company_name):
    url = f"https://boards-api.greenhouse.io/v1/boards/{subdomain}/jobs"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{"id": f"gh-{j['id']}", "title": j['title'], "url": j['absolute_url'], "location": j.get('location', {}).get('name', 'India')} for j in res.get('jobs', [])]
        new_jobs = process_matches(jobs, company_name, "greenhouse", subdomain)
        return {"success": True, "jobs_fetched": len(jobs), "new_jobs": new_jobs, "error": None}
    except Exception as e:
        error = str(e)
        print(f"Greenhouse fetch failed for {company_name}: {error}")
        return {"success": False, "jobs_fetched": 0, "new_jobs": 0, "error": error}

def fetch_lever(token, company_name):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        res = httpx.get(url, timeout=15).json()
        jobs = [{"id": f"lev-{j['id']}", "title": j['text'], "url": j['hostedUrl'], "location": j.get('categories', {}).get('location', 'India')} for j in res]
        new_jobs = process_matches(jobs, company_name, "lever", token)
        return {"success": True, "jobs_fetched": len(jobs), "new_jobs": new_jobs, "error": None}
    except Exception as e:
        error = str(e)
        print(f"Lever fetch failed for {company_name}: {error}")
        return {"success": False, "jobs_fetched": 0, "new_jobs": 0, "error": error}

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
        new_jobs = process_matches(jobs, company_name, "workday", subdomain)
        return {"success": True, "jobs_fetched": len(jobs), "new_jobs": new_jobs, "error": None}
    except Exception as e:
        error = str(e)
        print(f"Workday fetch failed for {company_name}: {error}")
        return {"success": False, "jobs_fetched": 0, "new_jobs": 0, "error": error}

if __name__ == "__main__":
    db_url = f"{SUPABASE_URL}/rest/v1/tracking_companies?is_active=eq.true"
    try:
        companies = httpx.get(db_url, headers=DB_HEADERS).json()
        total_companies = 0
        successful_scans = 0
        total_jobs = 0
        total_new_jobs = 0
        failed_companies = []
        for target in companies:
            total_companies += 1
            started_at = datetime.now(timezone.utc)
            if target['ats_type'] == 'workday':
                result = fetch_workday(target['token_or_subdomain'], target['company_name'])
            elif target['ats_type'] == 'greenhouse':
                result = fetch_greenhouse(target['token_or_subdomain'], target['company_name'])
            elif target['ats_type'] == 'lever':
                result = fetch_lever(target['token_or_subdomain'], target['company_name'])
            elif target['ats_type'] == 'oracle':
                result = fetch_oracle(target['token_or_subdomain'], target['company_name'])
            else:
                result = {
                    "success": False,
                    "jobs_fetched": 0,
                    "new_jobs": 0,
                    "error": f"Unsupported ATS type: {target['ats_type']} (supported: workday, greenhouse, lever, oracle)"
                }
            finished_at = datetime.now(timezone.utc)

            log_scan_run(
                target['company_name'],
                target['ats_type'],
                result["jobs_fetched"],
                result["new_jobs"],
                result["success"],
                result["error"],
                started_at,
                finished_at
            )

            total_jobs += result["jobs_fetched"]
            total_new_jobs += result["new_jobs"]
            if result["success"]:
                successful_scans += 1
            else:
                failed_companies.append(f"{target['company_name']} ({result['error']})")

        send_run_summary(total_companies, successful_scans, total_jobs, total_new_jobs, failed_companies)
        check_stale_scans(companies, STALE_SCAN_HOURS)
    except Exception as e: print(f"DB Error: {e}")
