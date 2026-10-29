#!/usr/bin/env python3
"""Experimental ONE-LESSON Rumble UNLISTED upload, GitHub-hosted browser.

MANUAL ONLY. Requires explicit attestations for content redistribution rights
and Rumble terms updated 2026-09-01. Never chooses exclusive distribution
rights, never changes visibility to public or private, never runs batches.
Do not retry ambiguous submissions until checking Rumble account.
"""
import argparse,json,os,re,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit

from browser_state import restricted_storage_state

ROOT=Path(__file__).resolve().parent
STAGING=ROOT/"backups"/"rumble"

_LAST_STAGE = "starting"

def stage(name):
    """Only print a fixed, non-sensitive stage name (never URLs, tokens or HTML)."""
    global _LAST_STAGE
    _LAST_STAGE = name
    print("RUMBLE_STAGE:", name, flush=True)

def safe_error_category(exc):
    """Avoid logging Playwright exceptions that can contain browser URLs."""
    message = str(exc)
    if "Timeout" in type(exc).__name__: return "timeout"
    if "TargetClosed" in type(exc).__name__ or "Target closed" in message: return "target_closed"
    if "net::ERR_" in message:
        match = re.search(r"net::ERR_[A-Z0-9_]+", message)
        return match.group(0) if match else "network_error"
    if "File" in message and ("large" in message.lower() or "size" in message.lower()):
        return "file_size"
    if "Browser has been closed" in message: return "browser_closed"
    if "Cannot read" in message: return "file_read"
    if "Cannot set" in message: return "file_selection_failed"
    if "strict mode violation" in message: return "selector_ambiguous"
    return "other_browser_error"

def lesson(seq):
    m=json.loads((ROOT/"course_manifest.json").read_text(encoding="utf8"))
    if m.get("privacy_required")!="unlisted" or not m.get("video_count"):
        raise RuntimeError("Course manifest check failed")
    if not isinstance(seq,int) or seq<2:
        raise RuntimeError("Sequence must be 2 or higher: lesson 1 was already uploaded to Rumble in the previous repository.")
    v=m["videos"][seq-1]
    if v.get("sequence")!=seq or v.get("extension")!=".mp4":
        raise RuntimeError("Unexpected lesson")
    return v

def inspect_owner_video_listing(page, title):
    """Read-only second-source verification after Submit.

    Rumble often stays on /upload.php after a successful submission. Look
    for exact lesson-title match and 'visibility: unlisted' in the owner's
    authenticated All Videos table. Never mistake HTTP transfer for publish.
    """
    page.goto("https://rumble.com/account/content?type=all",
              wait_until="domcontentloaded",timeout=60000)
    page.wait_for_timeout(3000)
    u=urlsplit(page.url)
    if u.scheme!="https" or u.hostname not in ("rumble.com","www.rumble.com"):
        raise RuntimeError("Rumble owner dashboard did not open")
    return page.evaluate("""title => {
      for(const tr of document.querySelectorAll('tr')) {
        const line=(tr.innerText||'').replace(/\\s+/g,' ').trim();
        if(!line.includes(title))continue;
        const urls=[...tr.querySelectorAll('a[href]')].map(a=>{
          try {const u=new URL(a.href,location.href);
            if(!['rumble.com','www.rumble.com'].includes(u.hostname))return '';
            return u.pathname;
          }catch{return ''}
        }).filter(x=>/^\\/v[a-z0-9]{5,}/i.test(x));
        return {found:urls.length>0,video_path:urls[0]||'',
                unlisted_label:/visibility:\\s*unlisted/i.test(line)};
      }
      return {found:false,video_path:'',unlisted_label:false};
    }""",title)


def required_approval():
    for k in ("RUMBLE_CONFIRM_RIGHTS","RUMBLE_CONFIRM_TERMS_2026","RUMBLE_CONFIRM_UNLISTED_UPLOAD"):
        if os.environ.get(k)!="YES":
            raise RuntimeError("Missing explicit operator approval: "+k)

def safe_writable_receipt(out,receipt):
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+"\n",encoding="utf8")

def run(seq,execute,diagnose_only=False):
    stage("course_manifest")
    v=lesson(seq)
    out=STAGING/f"{seq:04d}.json"
    print("Rumble lesson",seq,"privacy: UNLISTED requested.")
    if not execute:
        print("PREVIEW ONLY. This does not select, transfer, upload or submit a video.")
        if out.exists():
            existing=json.loads(out.read_text(encoding="utf8"))
            print("Existing Rumble lesson receipt:",existing.get("stage","unknown"))
        return
    if out.exists():
        raise RuntimeError("Rumble receipt already exists for sequence; NEVER retry uploaded lesson without reconciliation")
    required_approval()
    raw=os.environ.get("RUMBLE_BROWSER_STATE_B64","")
    if not raw:
        raise RuntimeError("No RUMBLE_BROWSER_STATE_B64 GitHub Secret")
    stage("decode_browser_state")
    state=restricted_storage_state(raw)
    # The shared Mail.ru downloader invokes ffprobe AFTER its 127 MB+ download.
    # Abort immediately, before downloading anything, if the binary is absent.
    if shutil.which("ffprobe") is None:
        raise RuntimeError("Missing ffprobe on runner. The workflow must install ffmpeg before fetching MP4.")
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="rumble-cloud-video-") as td:
        mp4=Path(td)/"original-lesson.mp4"
        stage("download_original_mp4")
        subprocess.run([sys.executable,str(ROOT/"mailru_fetch.py"),
                        "--url",v["source_url"],"--output",str(mp4)],
                       cwd=ROOT,check=True)
        if mp4.stat().st_size<10000 or mp4.stat().st_size>256000000:
            raise RuntimeError("Pilot original MP4 unexpected size; abort before any upload")
        print("Verified original MP4 exists in ephemeral runner, bytes:",mp4.stat().st_size)
        stage("original_mp4_validated")
        report={"provider":"rumble","sequence":seq,"source_url":v["source_url"],
                "source_path":v["relative_path"],"requested_visibility":"unlisted",
                "privacy_verified":False,"stage":"preparing",
                "submit_clicked":False}
        stage("start_playwright")
        with sync_playwright() as p:
            stage("launch_chrome")
            browser=p.chromium.launch(channel="chrome",headless=True)
            stage("load_browser_cookies")
            context=browser.new_context(storage_state=state)
            stage("open_browser_tab")
            page=context.new_page()
            event={"success_responses":0,"http_failure":0}
            def response_callback(resp):
                url=urlsplit(resp.url)
                if (url.scheme=="https" and
                    re.fullmatch(r"web[0-9]+\.rumble\.com",url.hostname or "") and
                    url.path=="/upload.php" and resp.request.method in ("POST","PUT")):
                    if 200<=resp.status<300: event["success_responses"]+=1
                    else: event["http_failure"]+=1
            page.on("response",response_callback)
            stage("navigate_rumble_upload_form")
            page.goto("https://rumble.com/upload.php",wait_until="domcontentloaded",timeout=65000)
            stage("locate_video_input")
            if page.locator("#Filedata").count()!=1:
                raise RuntimeError("Rumble upload form changed; #Filedata not unique")
            # File transfer can start as soon as file input changes.
            stage("select_127mb_mp4")
            page.locator("#Filedata").set_input_files(str(mp4))
            stage("mp4_selected")
            stage("set_video_title")
            page.locator("#title").fill(v["title"][:120])
            stage("set_video_description")
            page.locator("#description").fill((v.get("description") or "")[:1500])
            stage("select_unlisted_radio")
            unlisted=page.locator('label[for="visibility_unlisted"]')
            if unlisted.count()!=1:
                raise RuntimeError("Unlisted radio choice unavailable; no submission")
            unlisted.click(force=True,timeout=12000)
            if not page.locator("#visibility_unlisted").is_checked():
                raise RuntimeError("Owner-only UNLISTED radio not confirmed; no submission")

            # The site enforces a category through JS, although it does not
            # mark the HTML field required. "HowTo" is a verified category.
            stage("choose_HowTo_category")
            category=page.locator('input[name="primary-category"]')
            if category.count()!=1:
                raise RuntimeError("Rumble category picker missing")
            category.click(timeout=10000)
            option=page.get_by_text("HowTo",exact=True)
            if option.count()!=1:
                raise RuntimeError("Rumble HowTo category absent or ambiguous")
            option.click(timeout=10000)
            chosen=page.locator("#category_primary")
            if chosen.count()!=1 or not chosen.input_value().strip():
                raise RuntimeError("Rumble category was not registered")

            # Wait for the binary transfer BEFORE advancing. A 2xx chunk is
            # transport evidence, not yet a finished or published video.
            stage("wait_for_media_transfer")
            deadline=time.time()+900
            while time.time()<deadline:
                page.wait_for_timeout(4000)
                if event["http_failure"]>0:
                    raise RuntimeError("Rumble upload server returned an HTTP failure")
                if event["success_responses"]>0:
                    try:
                        page.wait_for_load_state("networkidle",timeout=10000)
                    except Exception:
                        continue
                    break
            if event["success_responses"]<1 or event["http_failure"]:
                raise RuntimeError("No successful Rumble media transfer; not advancing")
            if not page.locator("#visibility_unlisted").is_checked():
                raise RuntimeError("Unlisted visibility changed while transferring; not advancing")

            # First button is only a metadata -> licensing STEP TRANSITION.
            # Our earlier failed runs clicked a hidden consent checkbox BEFORE
            # this transition and aborted with a Playwright Error.
            stage("advance_metadata_to_license_step")
            first=page.locator("#submitForm")
            if first.count()!=1 or not first.is_enabled():
                raise RuntimeError("First Rumble step cannot advance")
            first.click(timeout=15000)
            rights_label=page.locator('label[for="crights"]')
            terms_label=page.locator('label[for="cterms"]')
            rights_label.wait_for(state="visible",timeout=20000)
            terms_label.wait_for(state="visible",timeout=20000)
            if page.get_by_text("Your licensing options",exact=True).count()==0:
                raise RuntimeError("Rumble licensing step not confirmed")
            if not page.locator("#visibility_unlisted").is_checked():
                raise RuntimeError("Rumble lost Unlisted before licensing")

            # For a backup, choose the real non-exclusive/non-monetized
            # Personal Use card. NEVER select either exclusive management.
            stage("select_personal_use_nonexclusive")
            personal=page.locator("div.wight-box.nfs").filter(has_text="Personal Use")
            if personal.count()!=1:
                raise RuntimeError("Personal Use license card not uniquely identified")
            personal_link=personal.locator("a").filter(has_text="Personal Use")
            if personal_link.count()!=1:
                raise RuntimeError("Personal Use license link missing or ambiguous")
            personal_link.click(timeout=10000)
            if not page.locator("#visibility_unlisted").is_checked():
                raise RuntimeError("Unlisted visibility changed during license choice")

            # Workflow inputs separately attest to distribution rights and
            # acceptance of the 2026-09-01 terms. Only the actual visible
            # checkbox labels are clicked in the user-approved execution.
            for key in ("crights","cterms"):
                stage("consent_"+key)
                label=page.locator('label[for="'+key+'"]')
                box=page.locator("#"+key)
                if label.count()!=1 or box.count()!=1 or not label.is_visible():
                    raise RuntimeError("Approved Rumble consent control unavailable: "+key)
                label.click(timeout=10000)
                if not box.is_checked():
                    raise RuntimeError("Rumble consent control not checked: "+key)

            stage("verify_final_submit_readiness")
            submit=page.locator("#submitForm2")
            if submit.count()!=1 or not submit.is_enabled():
                raise RuntimeError("Rumble final licensing Submit unavailable")

            if diagnose_only:
                stage("diagnostic_complete_NO_FINAL_SUBMIT")
                print("Rumble diagnostic passed all visible form stages. NO final Submit clicked.",flush=True)
                context.close()
                browser.close()
                return

            # Records the attempt BEFORE clicking. If Rumble responds ambiguously
            # the receipt retains its 'ambiguous' stage to prevent duplicate retry.
            report["category_selected"]="HowTo"
            report["license_requested"]="Personal Use (non-exclusive)"
            report["stage"]="submit_attempted"
            report["submit_clicked"]=True
            report["media_upload_http_successes"]=event["success_responses"]
            safe_writable_receipt(out,report)
            print("Finalizing Rumble UNLISTED upload; account and privacy must be reconciled.")
            stage("click_final_submitForm2")
            submit.click(timeout=12000)
            stage("await_post_submit_navigation")
            page.wait_for_timeout(10000)
            dest=urlsplit(page.url)
            report["stage"]="ambiguous_submitted_check_rumble_studio"
            if dest.scheme=="https" and dest.hostname in ("rumble.com","www.rumble.com"):
                candidate=re.search(r"/(v[a-z0-9]{5,})",dest.path,re.I)
                if candidate:
                    report["video_page"]="https://rumble.com/"+candidate.group(1)
                    report["stage"]="candidate_video_page"
            safe_writable_receipt(out,report)
            # A successful Rumble final Submit may not navigate. The prior
            # run DID create lesson 1 even though it stayed on upload.php.
            # Reconcile against the logged-in owner's content dashboard.
            stage("reconcile_owner_dashboard")
            try:
                for _ in range(6):
                    result=inspect_owner_video_listing(page,v["title"][:120])
                    if result["found"] and result["unlisted_label"]:
                        report["video_page"]="https://rumble.com"+result["video_path"]
                        report["stage"]="confirmed_in_rumble_owner_account"
                        report["privacy_verified"]=True
                        report["visibility_verified_from_owner_dashboard"]="unlisted"
                        report["account_listing_verified"]=True
                        break
                    page.wait_for_timeout(4000)
            except Exception:
                # Never discard a receipt for an ambiguous final Submit.
                report["reconciliation_incomplete"]=True
            safe_writable_receipt(out,report)
            browser.close()
        print("Rumble submission attempted, report stage:",report["stage"])
        if report["stage"]!="confirmed_in_rumble_owner_account":
            raise RuntimeError("Rumble final Submit outcome not confirmed in owner account. Receipt saved. Do not retry until reconciled.")
        print("Confirmed Rumble video in owner's list with UNLISTED visibility.")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--sequence",type=int,default=2)
    p.add_argument("--execute",action="store_true")
    p.add_argument("--diagnose-only",action="store_true",help="Transfer authorized lesson but NEVER submit")
    a=p.parse_args()
    if a.diagnose_only and not a.execute:
        raise RuntimeError("--diagnose-only requires --execute with explicit operator approval")
    run(a.sequence,a.execute,diagnose_only=a.diagnose_only)

if __name__=="__main__":
    try: main()
    except Exception as e:
        detail = str(e)[:180] if isinstance(e,(RuntimeError,ValueError)) else safe_error_category(e)
        print("Rumble pilot stopped at:",_LAST_STAGE,"exception:",type(e).__name__,
              "reason:",detail,flush=True)
        raise SystemExit(1)
