import time
import requests
import os
from openlocationcode import encode as olc_encode
from openlocationcode import SEPARATOR_POSITION_

# https://muscatoxblog.blogspot.com/2019/07/delving-into-renaults-new-api.html
API_KEY = os.getenv("API_KEY")
KAMEREON_API_KEY = os.getenv("KAMEREON_API_KEY")
BASE_URL = os.getenv("BASE_URL")
KEMERON_URL = os.getenv("KEMERON_URL")
TELEGRAM_KEY = os.getenv("TELEGRAM_KEY")
LOGINID = os.getenv("LOGINID")
PASSWORD = os.getenv("PASSWORD")
PLATE = os.getenv("PLATE")
CHAT_ID = int(os.getenv("CHAT_ID"))
PING_URL = os.getenv("PING_URL")
# Optional: a fresh 2FA code already known when the process starts (e.g. set right
# after reading it from email). Usually the code will instead arrive later via the
# /2fa Telegram command, since the code doesn't exist until we trigger the email.
TFA_CODE = os.getenv("TFA_CODE")

# Reused across reconnects (instead of a fresh requests.Session() each time) so that
# Gigya's "remember this device" cookies survive, which avoids re-triggering 2FA on
# every reconnect within the same run. See https://github.com/hacf-fr/renault-api/issues/2132
_gigya_session = requests.Session()


def start_tfa(session, reg_token):
    """Kick off Renault/Gigya's email-OTP 2FA challenge: sends a code to the account's
    registered email and returns the state needed to complete it once the code is known."""
    global API_KEY
    session.request("GET", BASE_URL + "/accounts.webSdkBootstrap", params={"APIKey": API_KEY})
    ucid = session.cookies.get("ucid", "")
    gmid = session.cookies.get("gmid", "")

    response = session.request(
        "GET",
        BASE_URL + "/accounts.tfa.initTFA",
        params={
            "provider": "gigyaEmail",
            "mode": "verify",
            "regToken": reg_token,
            "APIKey": API_KEY,
            "ucid": ucid,
            "gmid": gmid,
        },
    )
    gigya_assertion = response.json()["gigyaAssertion"]

    response = session.request(
        "GET",
        BASE_URL + "/accounts.tfa.email.getEmails",
        params={"gigyaAssertion": gigya_assertion, "APIKey": API_KEY},
    )
    email_id = response.json()["emails"][0]["id"]

    response = session.request(
        "GET",
        BASE_URL + "/accounts.tfa.email.sendVerificationCode",
        params={"emailID": email_id, "gigyaAssertion": gigya_assertion, "APIKey": API_KEY},
    )
    phv_token = response.json()["phvToken"]

    return {"regToken": reg_token, "gigyaAssertion": gigya_assertion, "phvToken": phv_token}


def complete_tfa(session, tfa_state, code):
    """Submit the emailed OTP code to finish the 2FA challenge. Raises ValueError on an
    invalid/expired code. Caller must re-POST accounts.login afterwards to get the cookie."""
    global API_KEY
    response = session.request(
        "GET",
        BASE_URL + "/accounts.tfa.email.completeVerification",
        params={
            "gigyaAssertion": tfa_state["gigyaAssertion"],
            "phvToken": tfa_state["phvToken"],
            "code": code,
            "APIKey": API_KEY,
        },
    )
    data = response.json()
    if "providerAssertion" not in data:
        raise ValueError(f"Invalid or expired 2FA code: {data}")

    response = session.request(
        "GET",
        BASE_URL + "/accounts.tfa.finalizeTFA",
        params={
            "gigyaAssertion": tfa_state["gigyaAssertion"],
            "providerAssertion": data["providerAssertion"],
            "tempDevice": "false",
            "regToken": tfa_state["regToken"],
            "APIKey": API_KEY,
        },
    )
    if response.json().get("errorCode", -1) != 0:
        raise ValueError(f"2FA finalization failed: {response.json()}")


def _finish_login(session, session_cookie):
    """Given a session with a valid Gigya login cookie, fetch the person id, mint a JWT
    and resolve the Kamereon account id. Shared by the plain and post-2FA login paths."""
    global API_KEY, KAMEREON_API_KEY, KEMERON_URL
    payload = {"login_token": session_cookie, "ApiKey": API_KEY}

    response = session.request("GET", BASE_URL + "/accounts.getAccountInfo", data=payload)
    print(f"Account info response: {response.text}")
    person_id = response.json()["data"]["personId"]

    jwt_payload = {**payload, "fields": "data.personId,data.gigyaDataCenter", "expiration": 900}
    response = session.request("GET", BASE_URL + "/accounts.getJWT", data=jwt_payload)
    jwt = response.json()["id_token"]

    headers = {
        "Content-Type": "application/vnd.api+json",
        "apikey": KAMEREON_API_KEY,
        "x-gigya-id_token": jwt,
    }
    response = session.request(
        "GET", KEMERON_URL + f"/persons/{person_id}?country=IT", headers=headers, data={}
    )
    print(response.text)
    account_id = response.json()["accounts"][0]["accountId"]

    return session, person_id, account_id, jwt, headers


def renault_login(tfa_code=None):
    """Log in to Renault/Gigya. Returns (session, person_id, account_id, jwt, headers, tfa_state).
    On success tfa_state is None. If Renault/Gigya demands 2FA (errorCode 403101, see
    https://github.com/hacf-fr/renault-api/issues/2132) and it can't be resolved immediately,
    person_id/account_id/jwt/headers are None and tfa_state is returned so the caller can
    complete it later (via TFA_CODE or the /2fa command) by calling complete_tfa() + retrying."""
    global API_KEY, LOGINID, PASSWORD, _gigya_session
    session = _gigya_session

    payload = {"ApiKey": API_KEY, "loginID": LOGINID, "password": PASSWORD}
    response = session.request("POST", BASE_URL + "/accounts.login", data=payload)
    data = response.json()

    if data.get("errorCode") == 403101:
        print("Renault/Gigya requires 2FA verification")
        tfa_state = start_tfa(session, data["regToken"])
        if tfa_code:
            try:
                complete_tfa(session, tfa_state, tfa_code)
                response = session.request("POST", BASE_URL + "/accounts.login", data=payload)
                sessionCookie = response.json()["sessionInfo"]["cookieValue"]
                return (*_finish_login(session, sessionCookie), None)
            except Exception as e:
                print(f"TFA_CODE failed, falling back to interactive 2FA: {e}")
        return session, None, None, None, None, tfa_state

    try:
        sessionCookie = data["sessionInfo"]["cookieValue"]
    except KeyError:
        print("Error: Failed to retrieve session cookie: ")
        print(response.text)
        return None, None, None, None, None, None

    print(f"Cookie: {sessionCookie}")
    return (*_finish_login(session, sessionCookie), None)


def get_vin(session, headers, account_id):
    global KEMERON_URL
    response = session.request(
        "GET",
        KEMERON_URL + f"/accounts/{account_id}/vehicles?country=IT",
        headers=headers,
        data={},
    )
    vin = response.json()["vehicleLinks"][0]["vin"]

    for vehicle in response.json()["vehicleLinks"]:
        print(
            vehicle["brand"],
            vehicle["vehicleDetails"]["model"]["label"],
            vehicle["vin"],
        )

    return vin


# https://renault-api.readthedocs.io/en/latest/endpoints.html#vehicle-data-endpoints


def get_charging_status(session, headers, account_id, vin):
    global KEMERON_URL
    response = session.request(
        "GET",
        KEMERON_URL
        + f"/accounts/{account_id}/kamereon/kca/car-adapter/v2/cars/{vin}/battery-status?country=IT",
        headers=headers,
        data={},
    )
    print(response.json())
    return response.json()


def get_cockpit(session, headers, account_id, vin):
    global KEMERON_URL
    response = session.request(
        "GET",
        KEMERON_URL
        + f"/accounts/{account_id}/kamereon/kca/car-adapter/v1/cars/{vin}/cockpit?country=IT",
        headers=headers,
        data={},
    )
    print(response.json())
    return response.json()


def get_location(session, headers, account_id, vin):
    response = session.request(
        "GET",
        KEMERON_URL
        + f"/accounts/{account_id}/kamereon/kca/car-adapter/v1/cars/{vin}/location?country=IT",
        headers=headers,
        data={},
    )
    print(response.json())
    return response.json()


def send_message(msg, parse_mode=""):
    global TELEGRAM_KEY, CHAT_ID
    with requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_KEY}/sendMessage?chat_id={CHAT_ID}&text={msg}&parse_mode={parse_mode}"
    ) as req:
        print(req.text)


def run():
    global API_KEY, KAMEREON_API_KEY, BASE_URL, KEMERON_URL, TELEGRAM_KEY, LOGINID, PASSWORD, PLATE, CHAT_ID
    print("Starting process")
    offset = 0
    last_charge_status = 0
    charging_status = None
    chat_id = CHAT_ID
    print(chat_id)
    count = 0
    session, person_id, account_id, jwt, headers, tfa_state = renault_login(tfa_code=TFA_CODE)
    print("session started")
    vin = get_vin(session, headers, account_id) if account_id else None
    print(f"vin: {vin}")
    if tfa_state:
        send_message(
            "Renault login needs 2FA: check your email for the code, "
            "then reply with /2fa <code>"
        )
    while True:
        print(count, 5 * 60 * 1.0 / 1)
        print(last_charge_status, charging_status)
        with requests.get(PING_URL) as req:
            if req.status_code != 200:
                print(f"Error {req.text}")
                time.sleep(60)
                continue
            print("Ping OK")
        with requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_KEY}/getUpdates?offset={offset}"
        ) as req:
            print(req.text)
            if req.status_code != 200:
                print(f"Error {req.text}")
                time.sleep(60)
                continue
            try:
                response = req.json()
                has_new_messages = len(response["result"]) > 0
                if account_id and (
                    (count > 5 * 60 * 1.0 / 1) or (has_new_messages)
                ):  # every 5 minutes
                    try:
                        car_state = get_charging_status(
                            session, headers, account_id, vin
                        )
                        car_cockpit = get_cockpit(session, headers, account_id, vin)
                        battery_status = car_state["data"]["attributes"]["batteryLevel"]
                        charging_status = int(
                            round(
                                10
                                * float(
                                    car_state["data"]["attributes"]["chargingStatus"]
                                )
                            )
                        )
                        plug_status = car_state["data"]["attributes"]["plugStatus"]
                        count = 0
                    except Exception as e:
                        print(e)
                        session, person_id, account_id, jwt, headers, tfa_state = renault_login()
                        if tfa_state:
                            send_message(
                                "Renault login needs 2FA: check your email for the "
                                "code, then reply with /2fa <code>"
                            )
                            continue
                        vin = get_vin(session, headers, account_id)
                        continue
                    if last_charge_status != charging_status:
                        # if not has_new_messages:
                        #     send_message("Charging status update")
                        #     send_message(f"Charge: {battery_status}%")
                        last_charge_status = charging_status
                        count = 0
                for message in response["result"]:
                    offset = message["update_id"] + 1
                    _chat_id = message["message"]["chat"]["id"]
                    if _chat_id != chat_id:
                        continue
                    text = message["message"]["text"]
                    print(text)
                    if "/2fa" in text:
                        if not tfa_state:
                            send_message("No 2FA verification is pending.")
                            continue
                        code = text.replace("/2fa", "").strip()
                        try:
                            complete_tfa(session, tfa_state, code)
                            # The 2FA challenge itself is now resolved (single-use code
                            # consumed) - clear it even if what follows below fails, so a
                            # retry doesn't try to replay an already-spent code.
                            tfa_state = None
                            payload = {
                                "ApiKey": API_KEY,
                                "loginID": LOGINID,
                                "password": PASSWORD,
                            }
                            resp = session.request(
                                "POST", BASE_URL + "/accounts.login", data=payload
                            )
                            sessionCookie = resp.json()["sessionInfo"]["cookieValue"]
                            session, person_id, account_id, jwt, headers = _finish_login(
                                session, sessionCookie
                            )
                            vin = get_vin(session, headers, account_id)
                            send_message("2FA verified, bot is now connected to the car.")
                        except Exception as e:
                            send_message(f"2FA verification failed: {e}")
                        continue
                    if not account_id:
                        send_message(
                            "Not connected to the car yet."
                            + (" Send /2fa <code>." if tfa_state else "")
                        )
                        continue
                    if "/charge" in text:
                        send_message(f"Charge: {battery_status}%")
                        send_message(
                            ("Not " if plug_status == 0 else " ") + "Plugged in"
                        )
                    if "/info" in text:
                        totalMileage = car_cockpit["data"]["attributes"]["totalMileage"]
                        fuelAutonomy = car_cockpit["data"]["attributes"]["fuelAutonomy"]
                        batteryAutonomy = car_state["data"]["attributes"][
                            "batteryAutonomy"
                        ]
                        send_message(f"Total Km: {totalMileage}Km")
                        send_message(f"Autonomy: [{fuelAutonomy}+{batteryAutonomy}]Km")
                    if "/vin" in text:
                        send_message(f"VIN: {vin}")
                    if "/plate" in text:
                        send_message(f"Plate: {PLATE}")
                    if "/location" in text:
                        location = get_location(session, headers, account_id, vin)
                        lon = location["data"]["attributes"]["gpsLongitude"]
                        lat = location["data"]["attributes"]["gpsLatitude"]
                        send_message(
                            f"[Location](https://www.openstreetmap.org/%3Fzoom=19%26mlat={lat}%26mlon={lon})",
                            parse_mode="MarkdownV2",
                        )
                        OLC = olc_encode(lat, lon, 10)
                        send_message(
                            f"<b>{OLC[:SEPARATOR_POSITION_]}%2B{OLC[SEPARATOR_POSITION_+1:]}</b>",
                            parse_mode="HTML",
                        )
                    if "/w3w" in text:
                        location = get_location(session, headers, account_id, vin)
                        lon = location["data"]["attributes"]["gpsLongitude"]
                        lat = location["data"]["attributes"]["gpsLatitude"]
                        w3w_url = f"https://api.what3words.com/v3/convert-to-3wa?coordinates={lat},{lon}"
                        with requests.get(
                            w3w_url,
                            headers={
                                "x-api-key": os.getenv("W3W_KEY"),
                                "format": "json",
                                "Referer": "https://developer.what3words.com/",
                                "Origin": "https://developer.what3words.com",
                                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Version/18.6 Safari/605.1.15 Ddg/18.6",
                            },
                        ) as w3w_req:
                            if w3w_req.status_code == 200:
                                w3w_data = w3w_req.json()
                                w3w_data = w3w_data["words"].split(".")
                                send_message(
                                    f"*[{w3w_data[0]}\\.{w3w_data[1]}\\.{w3w_data[2]}](https://w3w.co/{w3w_data[0]}\\.{w3w_data[1]}\\.{w3w_data[2]})*",
                                    parse_mode="MarkdownV2",
                                )
                            else:
                                send_message("Error fetching W3W data")
            except BaseException as e:
                print(e)
                continue
        count += 1
        time.sleep(1)


if __name__ == "__main__":
    while True:
        try:
            run()
        except Exception as e:
            print(e)
            time.sleep(120)
