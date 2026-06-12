import os
import sys
import json
import httpx
import re
import socket
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STALE_SCAN_HOURS = int(os.environ.get("STALE_SCAN_HOURS", "24"))
SUPPORTED_ATS_TYPES = ("workday", "greenhouse", "lever", "oracle")
DEFAULT_ORACLE_SITE_NAME = "CX_1"

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

def verify_network_health():
    """Circuit breaker checking if multiple baseline ATS platform domains resolve cleanly."""
    test_domains = ["boards-api.greenhouse.io", "api.lever.co"]
    for domain in test_domains:
        try:
            socket.gethostbyname(domain)
        except socket.gaierror:
            print(f"🚨 Network Check Failed: Cannot resolve baseline domain {domain}")
            return False
    return True

def parse_json_or_raise(response, context):
    try:
        return response.json()
    except json.JSONDecodeError as e:
        content_type = response.headers.get("content-type", "unknown")
        body_size = len(response.content or b"")
        raise ValueError(
            f"{context} returned non-JSON response "
            f"(status={response.status_code}, content_type={content_type}, body_bytes={body_size})"
        ) from e

def normalize_token(value):
    return str(value or "").strip().rstrip("/")

def build_oracle_host(token_or_subdomain):
    token = normalize_token(token_or_subdomain)
    if not token:
        raise ValueError("Missing Oracle token_or_subdomain")
    parsed = urlparse(token if "://" in token else f"https://{token}")
    host = (parsed.netloc or "").strip() or token.split("/")[0]
    if not host:
        raise ValueError(f"Invalid Oracle token_or_subdomain: {token_or_subdomain}")
    if "oraclecloud.com" not in host:
        host = f"{host}.fa.ocs.oraclecloud.com"
    return host

def build_or_extract_oracle_job_url(oracle_host, job_item):
    job_id = job_item.get("Id")
    if not job_id:
        raise ValueError("Oracle job item is missing Id")
    for key in ("ExternalURL", "ExternalUrl", "ApplyURL", "ApplyUrl", "JobURL", "JobUrl"):
        value = job_item.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    site_name_value = job_item.get("SiteName")
    site_name = site_name_value if isinstance(site_name_value, str) else DEFAULT_ORACLE_SITE_NAME
    return f"https://{oracle_host}/hcmUI/CandidateExperience/en/sites/{site_name}/job/{job_id}"

def build_workday_endpoints(token_or_subdomain):
    token = normalize_token(token_or_subdomain)
    if not token:
        raise ValueError("Missing Workday token_or_subdomain")
        
    if "myworkdayjobs.com" in token:
        parsed = urlparse(token if "://" in token else f"https://{token}")
        host = (parsed.hostname or "").strip()
        path_parts = [p for p in parsed.path.split("/") if p]
        
        if "cxs" in path_parts:
            cxs_idx = path_parts.index("cxs")
            tenant = path_parts[cxs_idx + 1]
            site = path_parts[cxs_idx + 2]
        else:
            tenant = host.split(".")[0]
            site = path_parts[0] if path_parts else tenant
            
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        return {
            "jobs_url": f"{base}/jobs",
            "job_details_url": f"{base}/jobDetails",
            "public_base_url": f"https://{host}"
        }
    
    parsed = urlparse(token if "://" in token else f"https://{token}")
    host = (parsed.hostname or "").strip()
    path_parts = [p for p in parsed.path.split("/") if p]

    if "cxs" in path_parts:
        cxs_idx = path_parts.index("cxs")
        tenant = path_parts[cxs_idx + 1]
        site = path_parts[cxs_idx + 2]
    else:
        tenant = host.split(".")[0] if host else token.split("/")[0]
        site = tenant

    if not host or "myworkdayjobs.com" not in host:
        host = f"{tenant}.myworkdayjobs.com"

    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    return {
        "jobs_url": f"{base}/jobs",
        "job_details_url": f"{base}/jobDetails",
        "public_base_url": f"https://{host}"
    }

def check_jd_experience(ats_type, job_id, token_or_subdomain, detail_url_override=None):
    text_to_scan = ""
    try:
        if ats_type == "oracle":
            oracle_host = build_oracle_host(token_or_subdomain)
            url = f"https://{oracle_host}/hcmRestApi/resources/latest/recruitingCandidateExperienceJobPostings/{job_id}"
            response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            res = parse_json_or_raise(response, f"Oracle JD fetch for {job_id}")
            text_to_scan = (res.get("Title", "") + " " + res.get("ShortDescription", "") + " " + res.get("LongDescription", "")).lower()
        elif ats_type == "greenhouse":
            clean_id = job_id.replace("gh-", "")
            url = f"https://boards-api.greenhouse.io/v1/boards/{token_or_subdomain}/jobs/{clean_id}"
            response = httpx.get(url, timeout=10)
            res = parse_json_or_raise(response, f"Greenhouse JD fetch for {job_id}")
            text_to_scan = res.get("content", "").lower()
        elif ats_type == "lever":
            clean_id = job_id.replace("lev-", "")
            url = f"https://api.lever.co/v0/postings/{token_or_subdomain}/{clean_id}"
            response = httpx.get(url, timeout=10)
            res = parse_json_or_raise(response, f"Lever JD fetch for {job_id}")
            lists = res.get("lists", {})
            requirements = lists.get("requirements", "") if isinstance(lists, dict) else ""
            text_to_scan = (res.get("description", "") + " " + requirements).lower()
        elif ats_type == "workday":
            url = detail_url_override or build_workday_endpoints(token_or_subdomain)["job_details_url"]
            payload = {"externalPath": job_id}
            # 🟢 Added follow_redirects=True to child deep-scans as well
            response = httpx.post(url, json=payload, headers={"Accept": "application/json"}, timeout=10, follow_redirects=True)
            res = parse_json_or_raise(response, f"Workday JD fetch for {job_id}")
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
    except Exception as e:
        print(f"Telegram error (len={len(message)}): {e}")

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
            stale_companies.append(f"{company['company_name']} (error checking scan status: {e})")
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
                    detail_url_override = job.get('detail_url')
                    
                    if not check_jd_experience(ats_type, scan_target_id, token_or_subdomain, detail_url_override=detail_url_override):
                        continue
                        
                    print(f"🔥 Validated Target Profile: {title} at {company_name} ({location})")
                    save_job_to_db(job_id, company_name, title, job_url)
                    send_telegram_alert(company_name, title, job_url, location)
                    new_jobs += 1
    return new_jobs

def fetch_oracle(subdomain, company_name):
    oracle_host = build_oracle_host(subdomain)
    # 🟢 CountryCode filter fallback mechanism helps guard against localized 404 router structural errors
    url = f"https://{oracle_host}/hcmRestApi/resources/latest/recruitingCandidateExperienceJobPostings?limit=50&countryCode=IN" 
    try:
        response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=15)
        if response.status_code in (400, 404):
            fallback_url = f"https://{oracle_host}/hcmRestApi/resources/latest/recruitingCandidateExperienceJobPostings?limit=25"
            response = httpx.get(fallback_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=15)
            
        res = parse_json_or_raise(response, f"Oracle jobs fetch for {company_name}")
        if not isinstance(res, dict):
            raise ValueError(f"Oracle jobs fetch returned unexpected type: {type(res).__name__}")
        jobs = []
        for item in res.get('items', []):
            if not isinstance(item, dict) or not item.get("Id") or not item.get("Title"):
                continue
            locations_list = [loc.get('Name', '') for loc in item.get('JobLocations', [])]
            locations_str = ", ".join(locations_list) if locations_list else "India"
            jobs.append({
                "id": f"or-{item['Id']}",
                "title": item['Title'],
                "url": build_or_extract_oracle_job_url(oracle_host, item),
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
        response = httpx.get(url, timeout=15)
        res = parse_json_or_raise(response, f"Greenhouse jobs fetch for {company_name}")
        if not isinstance(res, dict):
            raise ValueError(f"Greenhouse jobs fetch returned unexpected type: {type(res).__name__}")
        jobs = []
        for j in res.get('jobs', []):
            if not isinstance(j, dict) or not j.get("id") or not j.get("title") or not j.get("absolute_url"):
                continue
            jobs.append({
                "id": f"gh-{j['id']}",
                "title": j['title'],
                "url": j['absolute_url'],
                "location": j.get('location', {}).get('name', 'India') if isinstance(j.get('location'), dict) else 'India'
            })
        new_jobs = process_matches(jobs, company_name, "greenhouse", subdomain)
        return {"success": True, "jobs_fetched": len(jobs), "new_jobs": new_jobs, "error": None}
    except Exception as e:
        error = str(e)
        print(f"Greenhouse fetch failed for {company_name}: {error}")
        return {"success": False, "jobs_fetched": 0, "new_jobs": 0, "error": error}

def fetch_lever(token, company_name):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        response = httpx.get(url, timeout=15)
        res = parse_json_or_raise(response, f"Lever jobs fetch for {company_name}")
        
        raw_postings = []
        if isinstance(res, list):
            raw_postings = res
        elif isinstance(res, dict):
            raw_postings = res.get("data", res.get("postings", [])) if any(k in res for k in ("data", "postings")) else [res]
        else:
            raise ValueError(f"Lever jobs fetch returned unexpected type: {type(res).__name__}")
            
        jobs = []
        for j in raw_postings:
            if not isinstance(j, dict) or not j.get("id") or not j.get("text") or not j.get("hostedUrl"):
                continue
            jobs.append({
                "id": f"lev-{j['id']}",
                "title": j['text'],
                "url": j['hostedUrl'],
                "location": j.get('categories', {}).get('location', 'India') if isinstance(j.get('categories'), dict) else 'India'
            })
        new_jobs = process_matches(jobs, company_name, "lever", token)
        return {"success": True, "jobs_fetched": len(jobs), "new_jobs": new_jobs, "error": None}
    except Exception as e:
        error = str(e)
        print(f"Lever fetch failed for {company_name}: {error}")
        return {"success": False, "jobs_fetched": 0, "new_jobs": 0, "error": error}

def fetch_workday(subdomain, company_name):
    endpoints = build_workday_endpoints(subdomain)
    url = endpoints["jobs_url"]
    payload = {"appliedFacets": {"locationCountry": ["bc33aa3152ec42d4995f4791a106ed09"]}, "limit": 20, "offset": 0, "searchText": ""}
    try:
        # 🟢 FIXED: Added follow_redirects=True to handle Walmart's 303 gateway router proxy jumps smoothly
        response = httpx.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15, follow_redirects=True)
        res = parse_json_or_raise(response, f"Workday jobs fetch for {company_name}")
        if not isinstance(res, dict):
            raise ValueError(f"Workday jobs fetch returned unexpected type: {type(res).__name__}")
        jobs = []
        for j in res.get('jobPostings', []):
            if not isinstance(j, dict):
                continue
            external_path = j.get('externalPath', '')
            if isinstance(external_path, str) and external_path.startswith("http"):
                job_url = external_path
            else:
                job_url = f"{endpoints['public_base_url']}{external_path}"
            if not j.get("jobPostingId") or not j.get("title") or not job_url:
                continue
            jobs.append({
                "id": f"wd-{j['jobPostingId']}",
                "raw_path": external_path,
                "title": j['title'],
                "url": job_url,
                "location": j.get('locationsText', 'India'),
                "detail_url": endpoints["job_details_url"]
            })
        new_jobs = process_matches(jobs, company_name, "workday", subdomain)
        return {"success": True, "jobs_fetched": len(jobs), "new_jobs": new_jobs, "error": None}
    except Exception as e:
        error = str(e)
        print(f"Workday fetch failed for {company_name}: {error}")
        return {"success": False, "jobs_fetched": 0, "new_jobs": 0, "error": error}

if __name__ == "__main__":
    if not verify_network_health():
        print("🚨 Run Aborted: Network environment is experiencing DNS connection failures.")
        send_telegram_message("🚨 *Critical Automation Alert*: System runner lost DNS resolution capability. Processing cycle safely paused to maintain tracking state parity.", markdown=True)
        sys.exit(1)

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
                    "error": f"Unsupported ATS type: {target['ats_type']} (supported: {', '.join(SUPPORTED_ATS_TYPES)})"
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
