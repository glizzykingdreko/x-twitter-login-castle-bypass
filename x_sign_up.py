import os
import re
import secrets
import time
from datetime import date
import random
from random import choice
from urllib.parse import urlencode

from dotenv import load_dotenv

import jetfuel
from takion_castle import TakionCastle, browser_languages
from twitter_generator import ClientTransactionGenerator

load_dotenv()

WEB_BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
JFAPI = "https://jf.x.com/onboarding/web"
# u can use older chrome version, makes no difference.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
SEC_CH_UA = '"Not:A-Brand";v="8", "Chromium";v="157", "Google Chrome";v="157"'


def exit_ip_geo(solver_session):
    """Exit IP + country + timezone of this session's proxy, from one geo lookup through the proxy, so
    the token's &ip/&country and the request timezone match the connection and the caller never types
    them. Returns (ip, country, timezone); falls back to IP-only, then empty, if the lookup is down."""
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


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def uuid7() -> str:
    millis = int(time.time() * 1000)
    raw = bytearray(millis.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    text = raw.hex()
    return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"


def navigation_headers(accept_language: str) -> dict:
    return {
        "upgrade-insecure-requests": "1",
        "user-agent": USER_AGENT,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-site": "none",
        "sec-fetch-mode": "navigate",
        "sec-fetch-user": "?1",
        "sec-fetch-dest": "document",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": accept_language,
        "priority": "u=0, i",
    }


def jf_headers(method: str, guest_token: str, transaction_id: str, accept_language: str, timezone: str) -> dict:
    headers = {
        "x-jf-client-theme": "dark",
        "authorization": WEB_BEARER,
        "sec-ch-ua-platform": '"macOS"',
        "accept-language": accept_language,
        "timezone": timezone,
        "sec-ch-ua": SEC_CH_UA,
        "x-twitter-client-language": accept_language,
        "sec-ch-ua-mobile": "?0",
        "x-twitter-active-user": "yes",
        "x-client-transaction-id": transaction_id,
        "x-guest-token": guest_token,
        "user-agent": USER_AGENT,
        "x-jf-v": "JP-5",
    }
    if method == "POST":
        headers["content-type"] = "application/x-www-form-urlencoded"
    headers["accept"] = "*/*"
    if method == "POST":
        headers["origin"] = "https://x.com"
    headers["sec-fetch-site"] = "same-site"
    headers["sec-fetch-mode"] = "cors"
    headers["sec-fetch-dest"] = "empty"
    headers["referer"] = "https://x.com/"
    headers["accept-encoding"] = "gzip, deflate, br, zstd"
    headers["priority"] = "u=1, i"
    return headers


if __name__ == "__main__":
    if not os.path.exists("proxies.txt"):
        raise Exception("Create and fill proxies.txt with one proxy per line in format:\nip:port\nor\nip:port:user:pass")
    api_key = require_env("TAKION_API_KEY")
    email = require_env("X_EMAIL")
    accept_language = os.getenv("X_LANG", "en-gb")
    timezone = os.getenv("X_TIMEZONE")  # None -> auto-detected from the proxy exit IP below
    country = os.getenv("X_COUNTRY")    # None -> auto-detected from the proxy exit IP below
    signup_preferences = '{"discoverable_by_email":false,"send_email_newsletter":false}'
    proxies = [line for line in open("proxies.txt").read().splitlines() if line.strip()]
    print(f"proxies: {len(proxies)} in pool")

    proxy = choice(proxies)
    solver = TakionCastle(api_key, proxy, client_hello="chrome157")
    solver.cuid = str(uuid7())

    print("[1] Loading x.com and minting the guest token...")
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
    for _ in range(25):
        solver.cookiejar.clear()
        solver.cookiejar.set("__cuid", solver.cuid)
        try:
            root_response = solver.get("https://x.com/", headers=navigation_headers(accept_language))
        except RuntimeError:
            solver.set_proxy(choice(proxies))
            continue
        if "twitter-site-verification" in root_response.text:
            break
        solver.set_proxy(choice(proxies))
    else:
        print("[FAIL] no proxy in the pool returned the x.com page with the verification meta")
        raise SystemExit(1)

    for _ in range(10):
        try:
            sign_response = solver.get(sign_o_url, headers=asset_headers)
            break
        except RuntimeError:
            solver.set_proxy(choice(proxies))
    else:
        print("[FAIL] could not fetch the sign.o asset through any proxy")
        raise SystemExit(1)
    if not re.search(index_pattern, sign_response.text):
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

    guest_token = solver.cookiejar.get("gt") or solver.post(
        "https://api.x.com/1.1/guest/activate.json",
        headers={
            "authorization": WEB_BEARER,
            "user-agent": USER_AGENT,
        },
    ).json().get("guest_token")
    if not guest_token:
        print("[FAIL] could not obtain a guest token")
        raise SystemExit(1)
    solver.cookiejar.set("gt", guest_token)

    # u want the token to match the proxy details
    exit_ip, geo_country, geo_tz = exit_ip_geo(solver)
    country = country or geo_country or "it"
    timezone = timezone or geo_tz or "Europe/Paris"
    print(f"    exit_ip={exit_ip or 'unknown'} country={country} timezone={timezone}")
    language, languages = browser_languages(accept_language)

    transaction_generator = ClientTransactionGenerator(
        ondemand_file=sign_response.text,
        home_page=root_response.text,
    )
    print(f"    guest_token={guest_token}")
    print()

    print("[2] Warming onboarding state...")
    landing_response = solver.get(
        f"{JFAPI}/landing",
        headers=jf_headers("GET", guest_token, transaction_generator.generate("GET", "/onboarding/web/landing"), accept_language, timezone),
    )
    if landing_response.status_code >= 400:
        print(f"[FAIL] onboarding/landing bad status: {landing_response.status_code}")
        raise SystemExit(1)
    passkey_response = solver.get(
        f"{JFAPI}/remotes/passkey_one_fa",
        headers=jf_headers("GET", guest_token, transaction_generator.generate("GET", "/onboarding/web/remotes/passkey_one_fa"), accept_language, timezone),
        params={"form_id": "landing"},
    )
    if passkey_response.status_code >= 400:
        print(f"[FAIL] onboarding/passkey_one_fa bad status: {passkey_response.status_code}")
        raise SystemExit(1)
    print(f"    landing={landing_response.status_code} passkey={passkey_response.status_code}")
    print()

    print(f"[3] begin_login for {email}...")
    # You can actually pass just "latest" to the codec or nothing / false, you fall back automatically
    # to the latest live one, we keep it updated on our side. 
    castle_token = solver.generate_token("twitter", codec="EZK-f_Qb", stage="begin_login",
                                         ip=exit_ip, country=country, timezone=timezone, ua=USER_AGENT, language=language, languages=languages,
                                         username_len=len(email), identifier="email")
    # generate_token already synced solver.cuid + the __cuid cookie from the served value
    print(f"    exit_ip={exit_ip or 'unknown'} castle_token={castle_token[:40]}...")
    begin_response = solver.post(
        f"{JFAPI}/actions/begin_login",
        headers=jf_headers("POST", guest_token, transaction_generator.generate("POST", "/onboarding/web/actions/begin_login"), accept_language, timezone),
        data=urlencode({
            "username_or_email": email,
            "$castle_token": castle_token,
        }),
    )
    if begin_response.status_code != 200:
        print(f"[FAIL] begin_login refused [{begin_response.status_code}], rotate the proxy")
        raise SystemExit(1)
    begin = jetfuel.parse_response(begin_response.content)
    session_token = begin["session_token"]
    # Accepted = begin_login returned a session_token (Castle passed). next_action just names the next
    # step for the caller to drive — for email login that's /actions/finish_login_email_verification,
    # for other flows it differs — so don't gate on a specific next_action, only on the session_token.
    if not session_token:
        reason = begin["error_message"] or begin["next_action"] or "no session_token"
        print(f"[FAIL] begin_login was not accepted: {reason} "
              f"(session_token={session_token!r}, next_action={begin['next_action']!r}, "
              f"size={len(begin_response.content or b'')}B)")
        raise SystemExit(1)
    print(f"    session_token={session_token}")
    print(f"    next_action={begin['next_action']}")
    print()

    # [4] finish_login_email_verification. X mails a code to the address and the form asks for it, another
    # Castle token is needed. Pass the same username_len / identifier for continuity
    next_step = (begin["next_action"] or "").rsplit("/", 1)[-1]
    if next_step != "finish_login_email_verification":
        print(f"    next step is {next_step or 'none'}, not the email code, the rest of the flow is on you")
        raise SystemExit(0)
    print(f"[4] finish_login_email_verification, X mailed a code to {email}")
    code = input("    verification code: ").strip().replace(" ", "")
    # Mint right after the code is typed: the token states when the page submitted it.
    code_token = solver.generate_token("twitter", codec="EZK-f_Qb", stage="finish_login_email_verification",
                                       ip=exit_ip, country=country, timezone=timezone, ua=USER_AGENT, language=language, languages=languages,
                                       username_len=len(email), identifier="email")
    code_response = solver.post(
        f"{JFAPI}/actions/finish_login_email_verification",
        headers=jf_headers("POST", guest_token, transaction_generator.generate("POST", "/onboarding/web/actions/finish_login_email_verification"), accept_language, timezone),
        data=urlencode({
            "session_token": session_token,
            "$castle_token": code_token,
            "code": code,
            "destination": email,
        }),
    )
    if code_response.status_code != 200:
        print(f"[FAIL] finish_login_email_verification refused [{code_response.status_code}], rotate the proxy")
        raise SystemExit(1)
    verify = jetfuel.parse_response(code_response.content)
    error = verify["error_message"] or ""
    if error:
        # A wrong or expired code is answered with a message about the code: the token itself was accepted.
        print(f"[FAIL] code not accepted: {error}")
        raise SystemExit(1)
    print(f"[PASS] code accepted")
    print(f"    next_action={verify['next_action']}")
    auth_token = solver.cookiejar.get("auth_token")
    if auth_token:
        print(f"    auth_token={auth_token}")
        print(f"    session cookies: {', '.join(cookie.name for cookie in solver.cookiejar)}")

    # Rest of the flow is on you, castle part has been handled.