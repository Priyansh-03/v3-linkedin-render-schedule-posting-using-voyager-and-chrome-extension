from flask import Flask, redirect, render_template, request, jsonify
from flask_cors import CORS
import os
import requests
import json
import time
import sqlite3
from datetime import datetime, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from linkedin_api import Linkedin
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for extension requests

# Stored in Render Environment Variables
CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

STATE = "demo123"
SCOPE = "openid profile email"

DB_PATH = os.getenv("SCHEDULE_DB_PATH", "schedule.db")


def db_connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_at TEXT NOT NULL,
                status TEXT NOT NULL,
                text TEXT NOT NULL,
                cookies_json TEXT NOT NULL,
                last_error TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_posts_run ON scheduled_posts(status, run_at);")
        conn.commit()
    finally:
        conn.close()


def parse_iso_datetime(value: str) -> datetime:
    """
    Accept ISO strings like '2025-12-18T12:34:56.000Z' or with offset.
    If naive, treat as UTC.
    """
    if not value:
        raise ValueError("Missing run_at")
    v = str(value).strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def post_to_linkedin_with_cookies(cookies: dict, text: str):
    api = create_linkedin_api_with_cookies(cookies)
    return api.create_post(text=text)


def process_due_posts():
    """
    Runs on the server. Picks due scheduled posts and posts them to LinkedIn.
    Uses a DB 'claim' update so multiple processes don't duplicate-post.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = db_connect()
    try:
        due = conn.execute(
            """
            SELECT id, run_at, text, cookies_json
            FROM scheduled_posts
            WHERE status = 'pending' AND run_at <= ?
            ORDER BY run_at ASC
            LIMIT 25
            """,
            (now,),
        ).fetchall()

        for row in due:
            post_id = int(row["id"])
            claimed = conn.execute(
                "UPDATE scheduled_posts SET status = 'processing' WHERE id = ? AND status = 'pending'",
                (post_id,),
            ).rowcount
            conn.commit()
            if claimed != 1:
                continue

            try:
                cookies = json.loads(row["cookies_json"] or "{}")
                text = row["text"] or ""
                result = post_to_linkedin_with_cookies(cookies, text)
                if result.get("success"):
                    conn.execute(
                        "UPDATE scheduled_posts SET status = 'done', last_error = NULL WHERE id = ?",
                        (post_id,),
                    )
                else:
                    conn.execute(
                        "UPDATE scheduled_posts SET status = 'failed', last_error = ? WHERE id = ?",
                        (str(result.get("error") or "Failed to create post"), post_id),
                    )
                conn.commit()
            except Exception as e:
                conn.execute(
                    "UPDATE scheduled_posts SET status = 'failed', last_error = ? WHERE id = ?",
                    (str(e), post_id),
                )
                conn.commit()
    finally:
        conn.close()


init_db()

# Background scheduler (server-side). This is what allows posting even if the user's laptop is off.
scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(process_due_posts, "interval", seconds=30, id="process_due_posts", replace_existing=True)
scheduler.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/compose")
def compose():
    return render_template("compose.html")

@app.route("/outspark-demo")
def outspark_demo():
    # Demo page that mimics Outspark behavior: requests cookies from extension and stores in website IndexedDB.
    return render_template("outspark_demo.html")


@app.route("/login")
def login():
    # Build LinkedIn authorization URL
    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state={STATE}"
        f"&scope={SCOPE}"
    )
    return redirect(auth_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")

    # Exchange code → access_token
    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }

    try:
        token_response = requests.post(token_url, data=data)
        token_data = token_response.json()
        access_token = token_data.get("access_token", "No Token Returned")
    except Exception as e:
        access_token = f"Error: {e}"

    # WhatsApp-like UI
    return f"""
    <html>
    <head>
        <title>LinkedIn OAuth</title>
    </head>
    <body style="margin:0; font-family:Arial; background:#f0f2f5;">

        <div style='
            display:flex;
            justify-content:center;
            align-items:center;
            height:100vh;
        '>

            <div style='
                background:white;
                width:80%;
                max-width:1000px;
                height:70%;
                display:flex;
                border-radius:14px;
                overflow:hidden;
                box-shadow:0 4px 20px rgba(0,0,0,0.1);
            '>

                <!-- LEFT PANEL -->
                <div style="
                    flex:1;
                    padding:40px;
                    background:#fff;
                ">
                    <h2 style='margin-top:0;'>LinkedIn OAuth Successful</h2>

                    <p><b>Authorization Code:</b><br>{code}</p>
                    <p><b>State:</b><br>{state}</p>

                    <p><b>Access Token:</b><br>
                        <div style='
                            background:#f5f5f5;
                            padding:10px;
                            border-radius:6px;
                            font-size:14px;
                            word-wrap:break-word;
                        '>{access_token}</div>
                    </p>
                </div>
            </div>
        </div>

    </body>
    </html>
    """

def create_linkedin_api_with_cookies(cookies):
    """
    Helper function to create a Linkedin API instance with cookies.
    """
    # Initialize API without loading cookies from file
    api = Linkedin(skip_cookie_load=True)
    
    # Set cookies directly
    for name, value in cookies.items():
        api.client.session.cookies.set(name, value, domain='.linkedin.com')
    
    # Set CSRF token from JSESSIONID
    jsessionid = cookies.get("JSESSIONID", "").replace('"', '')
    if jsessionid:
        api.client.session.headers.update({
            "csrf-token": jsessionid
        })
    
    return api


@app.route("/verify-cookies", methods=["POST"])
def verify_cookies():
    """
    Verify that the provided cookies are valid by attempting to get user profile.
    """
    data = request.get_json()
    cookies = data.get("cookies")
    
    if not cookies:
        return jsonify({"status": "error", "message": "Missing cookies."}), 400
    
    try:
        api = create_linkedin_api_with_cookies(cookies)
        profile = api.get_user_profile()
        
        if profile and profile.get("plain_id"):
            return jsonify({
                "status": "success",
                "message": "Cookies are valid",
                "profile": profile
            })
        else:
            return jsonify({"status": "error", "message": "Invalid cookies."}), 401
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 401


@app.route("/get-profile", methods=["POST"])
def get_profile():
    """
    Get user profile using provided cookies.
    """
    data = request.get_json()
    cookies = data.get("cookies")
    
    if not cookies:
        return jsonify({"status": "error", "message": "Missing cookies."}), 400
    
    try:
        api = create_linkedin_api_with_cookies(cookies)
        profile = api.get_user_profile()
        
        return jsonify({
            "status": "success",
            "profile": profile
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/post-to-linkedin", methods=["POST"])
def post_to_linkedin():
    # Expect cookies to be passed from the client (extension or web)
    data = request.get_json()
    if not data:
         # Fallback to form for legacy or mixed usage, but primarily JSON now
         post_text = request.form.get("text")
         # We need cookies!
         return jsonify({"status": "error", "message": "Missing request data."}), 400

    cookies = data.get("cookies")
    post_text = data.get("text")
    
    if not cookies or not post_text:
        return jsonify({"status": "error", "message": "Missing cookies or text content."}), 400

    try:
        # Create API instance with cookies
        api = create_linkedin_api_with_cookies(cookies)
        
        # Create Post
        result = api.create_post(text=post_text)
        
        if result.get("success"):
            return jsonify({
                "status": "success",
                "message": "Post created successfully!",
                "post_url": result.get("post_url", "")
            })
        else:
            return jsonify({
                "status": "error",
                "message": result.get("error", "Failed to create post")
            }), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/start-browser-login", methods=["POST"])
def start_browser_login():
    """
    Launches a visible Chromium browser.
    User logs in manually.
    Backend waits for 'li_at' cookie.
    Extracts and returns cookies.
    """
    extracted_cookies = {}
    
    try:
        with sync_playwright() as p:
            # Step 1: Launch Browser (Visible)
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            # Step 2: Go to LinkedIn
            page.goto("https://www.linkedin.com/login")

            # Step 3: Wait loop for 'li_at' cookie
            print("Waiting for user to login...")
            max_retries = 60 # Wait up to 60 seconds (approx)
            logged_in = False
            
            for _ in range(max_retries):
                cookies = context.cookies()
                cookie_dict = {c['name']: c['value'] for c in cookies}
                
                if 'li_at' in cookie_dict and 'JSESSIONID' in cookie_dict:
                    extracted_cookies = cookie_dict
                    logged_in = True
                    break
                
                time.sleep(1) # Check every 1 second
            
            browser.close()
            
            if logged_in:
                return jsonify({
                    "status": "success", 
                    "message": "Cookies extracted successfully", 
                    "cookies": {
                        "li_at": extracted_cookies.get("li_at"),
                        "JSESSIONID": extracted_cookies.get("JSESSIONID")
                    }
                })
            else:
                return jsonify({"status": "error", "message": "Login timeout or failed"}), 408

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/schedule-post", methods=["POST"])
def schedule_post():
    """
    Schedule a LinkedIn post server-side.
    The backend stores cookies+text and will post at the chosen time even if the laptop is off.
    """
    data = request.get_json() or {}
    cookies = data.get("cookies") or {}
    text = StringOrNone = data.get("text")
    run_at = data.get("run_at")

    if not isinstance(cookies, dict) or not cookies.get("li_at") or not cookies.get("JSESSIONID"):
        return jsonify({"status": "error", "message": "Missing LinkedIn cookies."}), 400
    if not text or not str(text).strip():
        return jsonify({"status": "error", "message": "Missing post text."}), 400
    try:
        run_dt = parse_iso_datetime(run_at)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Invalid run_at: {e}"}), 400

    conn = db_connect()
    try:
        created_at = datetime.now(timezone.utc).isoformat()
        run_at_iso = run_dt.isoformat()
        cur = conn.execute(
            """
            INSERT INTO scheduled_posts(created_at, run_at, status, text, cookies_json, last_error)
            VALUES(?, ?, 'pending', ?, ?, NULL)
            """,
            (created_at, run_at_iso, str(text).strip(), json.dumps({"li_at": cookies["li_at"], "JSESSIONID": cookies["JSESSIONID"]})),
        )
        conn.commit()
        return jsonify(
            {
                "status": "success",
                "id": cur.lastrowid,
                "run_at": run_at_iso,
                "message": "Post scheduled.",
            }
        )
    finally:
        conn.close()


@app.route("/clear-scheduled", methods=["POST"])
def clear_scheduled():
    """
    Best-effort: clear all scheduled posts (used on logout).
    """
    conn = db_connect()
    try:
        conn.execute("DELETE FROM scheduled_posts;")
        conn.commit()
        return jsonify({"status": "success", "message": "Cleared scheduled posts."})
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
