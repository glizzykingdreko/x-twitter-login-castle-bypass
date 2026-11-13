import argparse
import os
import re
import uuid
from urllib.parse import urlencode
from random import choice

from dotenv import load_dotenv

import jetfuel
from takion_castle import TakionCastle
from twitter_generator import ClientTransactionGenerator

load_dotenv()

# The public web app Bearer. It ships in x.com's own JS, it's not a secret and it's the same
# for every session, the per-session auth is the guest token and the Castle token.
WEB_BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
# u can use older chrome version, makes no difference.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
SEC_CH_UA = '"Google Chrome";v="157", "Not_A Brand";v="8", "Chromium";v="157"'
JFAPI = "https://jf.x.com/onboarding/web"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def navigation_headers(accept_language: str) -> dict:
    return {
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "upgrade-insecure-requests": "1",
        "user-agent": USER_AGENT,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "sec-fetch-site": "none",
        "sec-fetch-mode": "navigate",
        "sec-fetch-user": "?1",
        "sec-fetch-dest": "document",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": accept_language,
        "priority": "u=0, i",
    }


def jf_headers(method: str, active_user: str, guest_token: str, transaction_id: str, accept_language: str, timezone: str) -> dict:
    headers = {
        "x-jf-client-theme": "dark",
        "authorization": WEB_BEARER,
        "sec-ch-ua-platform": '"macOS"',
        "accept-language": accept_language,
        "timezone": timezone,
        "sec-ch-ua": SEC_CH_UA,
        "x-twitter-client-language": accept_language,
        "sec-ch-ua-mobile": "?0",
        "x-twitter-active-user": active_user,
        "x-client-transaction-id": transaction_id,
        "x-guest-token": guest_token,
        "user-agent": USER_AGENT,
        "x-jf-v": "JP-5",
    }
    if method.upper() == "POST":
        headers["content-type"] = "application/x-www-form-urlencoded"
    headers["accept"] = "*/*"
    if method.upper() == "POST":
        headers["origin"] = "https://x.com"
    headers["sec-fetch-site"] = "same-site"
    headers["sec-fetch-mode"] = "cors"
    headers["sec-fetch-dest"] = "empty"
    headers["referer"] = "https://x.com/"
    headers["accept-encoding"] = "gzip, deflate, br, zstd"
    headers["priority"] = "u=1, i"
    return headers


def exit_ip_geo(solver_session):
    try:
        d = solver_session.get("http://ip-api.com/json/?fields=status,query,countryCode,timezone").json()
        if d.get("status") == "success" and d.get("query"):
            return d["query"], (d.get("countryCode") or "").lower() or None, d.get("timezone")
    except Exception:
        pass
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            text = (solver_session.get(url).text or "").strip()
            if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", text):
                return text, None, None
        except Exception:
            continue
    return "", None, None


if __name__ == "__main__":
    if not os.path.exists("proxies.txt"):
        raise Exception("Create and fill proxies.txt with one proxy per line in format:\nip:port\nor\nip:port:user:pass")
    parser = argparse.ArgumentParser(description="Log into an X / Twitter account through the Castle-gated onboarding flow.")
    parser.add_argument("--username", "--email", dest="username")
    parser.add_argument("--password")
    parser.add_argument("--lang", default="en-GB")
    parser.add_argument("--timezone", default=None, help="request timezone; loaded from the proxy exit IP if omitted")
    parser.add_argument("--country", default=None, help="token timezone/locale; loaded from the proxy exit IP if omitted")
    args = parser.parse_args()

    api_key = require_env("TAKION_API_KEY")
    username = args.username or require_env("TWITTER_USERNAME")
    password = args.password or require_env("TWITTER_PASSWORD")
    accept_language = args.lang
    timezone = args.timezone
    country = args.country
    proxies = [line for line in open("proxies.txt").read().splitlines() if line.strip()]
    print(f"proxy: {proxies[0].split(':')[0]}... ({len(proxies)} in pool)")

    solver = TakionCastle(api_key, choice(proxies), client_hello="chrome156")
    # Seed the session's __cuid before the first page load so every request, page and token
    # alike, carries the same device id. generate_token adopts whatever the service returns.
    solver.cuid = str(uuid.uuid4())

    print("[1] Seeding transaction id from the x-web build...")
    sign_o_url = "https://abs.twimg.com/x-web/x-web/assets/sign.o-NnfXKGyX.js"
    index_pattern = r"\(\w\[\d{1,2}\],\s*16\)"
    asset_headers = {
        "user-agent": USER_AGENT,
        "accept": "*/*",
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-dest": "script",
        "referer": "https://x.com/",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": accept_language,
    }
    root_response = None
    for _ in range(15):
        solver.cookiejar.clear()
        solver.cookiejar.set("__cuid", solver.cuid)
        root_response = solver.get("https://x.com/", headers=navigation_headers(accept_language))
        if "twitter-site-verification" in root_response.text:
            break
        solver.set_proxy(choice(proxies))
    else:
        print("[FAIL] x.com kept returning a page without the verification meta; rotate proxies")
        raise SystemExit(1)

    sign_response = solver.get(sign_o_url, headers=asset_headers)
    if not re.search(index_pattern, sign_response.text):
        # sign.o rotated: the entry loader lists the current chunk hashes, one holds the indices.
        entry_hash = re.search(r"entry-client-logged-out-([A-Za-z0-9_-]+)\.js", root_response.text).group(1)
        entry = solver.get(f"https://abs.twimg.com/x-web/x-web/entry-client-logged-out-{entry_hash}.js", headers=asset_headers).text
        sign_response = None
        for name in re.findall(r"assets/([A-Za-z0-9_.]+-[A-Za-z0-9_-]{6,})\.js", entry):
            candidate = solver.get(f"https://abs.twimg.com/x-web/x-web/assets/{name}.js", headers=asset_headers)
            if re.search(index_pattern, candidate.text):
                sign_response = candidate
                break
        if sign_response is None:
            print("[FAIL] could not locate the sign.o key-byte chunk in the x-web build")
            raise SystemExit(1)

    # The x-web root no longer inlines gt=, so mint a guest token from the activate endpoint.
    guest_token = solver.cookiejar.get("gt") or solver.post(
        "https://api.x.com/1.1/guest/activate.json",
        headers={"authorization": WEB_BEARER, "user-agent": USER_AGENT},
    ).json().get("guest_token")
    if not guest_token:
        print("[FAIL] could not obtain a guest token")
        raise SystemExit(1)
    solver.cookiejar.set("gt", guest_token)

    # u want the token to match the proxy details
    exit_ip, geo_country, geo_tz = exit_ip_geo(solver)
    country = country or geo_country or "it"
    timezone = timezone or geo_tz or "Europe/Rome"
    print(f"    exit_ip={exit_ip or 'unknown'} country={country} timezone={timezone}")

    transaction_generator = ClientTransactionGenerator(
        ondemand_file=sign_response.text,
        home_page=root_response.text,
    )
    print(f"    guest_token={guest_token}\n")

    # [2] Warm the onboarding state the way the login page does before you type, a landing GET
    # and the passkey remote. Skipping these makes begin_login look out of order.
    print("[2] Warming onboarding state...")
    landing_response = solver.get(
        f"{JFAPI}/landing",
        headers=jf_headers("GET", "no", guest_token, transaction_generator.generate("GET", "/onboarding/web/landing"), accept_language, timezone),
    )
    if landing_response.status_code >= 400:
        print(f"[FAIL] onboarding/landing bad status: {landing_response.status_code}")
        raise SystemExit(1)
    passkey_response = solver.get(
        f"{JFAPI}/remotes/passkey_one_fa",
        headers=jf_headers("GET", "no", guest_token, transaction_generator.generate("GET", "/onboarding/web/remotes/passkey_one_fa"), accept_language, timezone),
        params={"form_id": "landing"},
    )
    if passkey_response.status_code >= 400:
        print(f"[FAIL] onboarding/passkey_one_fa bad status: {passkey_response.status_code}")
        raise SystemExit(1)
    print(f"    landing={landing_response.status_code} passkey={passkey_response.status_code}\n")

    # [3] begin_login. First Castle-gated call, the WcmFKZlD token rides in the body as
    # $castle_token. A good answer carries a session_token and points at the next subtask.
    print(f"[3] begin_login for {username}...")
    begin_response = solver.post(
        f"{JFAPI}/actions/begin_login",
        headers=jf_headers("POST", "yes", guest_token, transaction_generator.generate("POST", "/onboarding/web/actions/begin_login"), accept_language, timezone),
        data=urlencode({
            "username_or_email": username,
            "$castle_token": solver.generate_token("twitter", codec="WcmFKZlD", stage="begin_login", ip=exit_ip, country=country, ua=USER_AGENT, build="156"),
        }),
    )
    if begin_response.status_code != 200:
        print(f"[BLOCKED] begin_login refused [{begin_response.status_code}], rotate to a clean proxy")
        raise SystemExit(1)
    begin = jetfuel.parse_response(begin_response.content)
    session_token = begin["session_token"]
    if not session_token:
        error = begin["error_message"] or ""
        if error.startswith("user_not_found:") or "find an active" in error:
            print(f"[FAIL] no active account for that username: {error}")
        elif "temporarily limited" in error:
            print(f"[BLOCKED] session flagged: {error}")
        else:
            print(f"[FAIL] begin_login gave no session_token: {error or begin_response.content[:120]}")
        raise SystemExit(1)
    print(f"    session_token={session_token} next_action={begin['next_action']}\n")

    # [4] login_enter_password. Same session, a fresh Castle token, and the session_token from
    # step 3. A clean pass drops the auth_token cookie, that cookie is the logged-in session.
    print("[4] login_enter_password...")
    password_response = solver.post(
        f"{JFAPI}/actions/login_enter_password",
        headers=jf_headers("POST", "yes", guest_token, transaction_generator.generate("POST", "/onboarding/web/actions/login_enter_password"), accept_language, timezone),
        data=urlencode({
            "username": username,
            "password": password,
            "session_token": session_token,
            "$castle_token": solver.generate_token("twitter", codec="WcmFKZlD", stage="login_enter_password", ip=exit_ip, country=country, ua=USER_AGENT, build="156"),
        }),
    )
    if password_response.status_code != 200:
        print(f"[BLOCKED] login_enter_password refused [{password_response.status_code}], rotate the proxy")
        raise SystemExit(1)
    password_result = jetfuel.parse_response(password_response.content)
    error = password_result["error_message"] or ""
    if error:
        print(f"[FAIL] password rejected: {error}")
        raise SystemExit(1)
    auth_token = solver.cookiejar.get("auth_token")
    if auth_token:
        print(f"[PASS] logged in as {username}")
        print(f"    auth_token={auth_token}")
        print(f"    session cookies: {', '.join(cookie.name for cookie in solver.cookiejar)}")
        raise SystemExit(0)

    # No auth_token means another subtask (2FA, an email code, an ACID check). next_action names
    # it, answer it the same way, a fresh Castle token on this same session against that action.
    print(f"[PASS] password accepted, further subtask required: next_action={password_result['next_action']}")
