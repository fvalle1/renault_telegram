import time
import requests
import os

# https://muscatoxblog.blogspot.com/2019/07/delving-into-renaults-new-api.html
API_KEY = os.getenv("API_KEY")
KAMEREON_API_KEY = os.getenv("KAMEREON_API_KEY")
BASE_URL = os.getenv("BASE_URL")
KEMERON_URL = os.getenv("KEMERON_URL")
TELEGRAM_KEY = os.getenv("TELEGRAM_KEY")
LOGINID = os.getenv("LOGINID")
PASSWORD = os.getenv("PASSWORD")
PLATE = os.getenv("PLATE")
CHAT_ID = os.getenv("CHAT_ID")


def renault_login():
    session = requests.Session()

    payload = {'ApiKey': API_KEY,
               'loginID': LOGINID,
               'password': PASSWORD}
    files = [
    ]
    headers = {}
    response = session.request(
        "POST",
        BASE_URL +
        "/accounts.login",
        headers=headers,
        data=payload,
        files=files)
    sessionCookie = response.json()["sessionInfo"]["cookieValue"]
    print(f"Cookie: {sessionCookie}")

    payload = {'login_token': sessionCookie,
               'ApiKey': API_KEY,
               "fields": "data.personId,data.gigyaDataCenter"}
    files = []
    headers = {}

    response = session.request(
        "GET",
        BASE_URL +
        "/accounts.getAccountInfo",
        headers=headers,
        data=payload,
        files=files)
    person_id = response.json()["data"]["personId"]

    print(f"person ID: {person_id}")

    response = session.request(
        "GET",
        BASE_URL +
        "/accounts.getJWT",
        headers=headers,
        data=payload,
        files=files)
    jwt = response.json()["id_token"]

    print(f"JWT: {jwt}")

    headers = {}
    payload = {}
    headers = {
        'Content-Type': 'application/vnd.api+json',
        'apikey': KAMEREON_API_KEY,
        'x-gigya-id_token': jwt,
    }

    response = session.request(
        "GET",
        KEMERON_URL +
        f"/persons/{person_id}?country=IT",
        headers=headers,
        data=payload)

    print(response.json()["accounts"][0])
    account_id = response.json()["accounts"][0]["accountId"]

    return session, person_id, account_id, jwt, headers


def get_vin(session, headers, account_id):
    response = session.request(
        "GET",
        KEMERON_URL +
        f"/accounts/{account_id}/vehicles?country=IT",
        headers=headers,
        data={})
    vin = response.json()["vehicleLinks"][0]['vin']

    for vehicle in response.json()["vehicleLinks"]:
        print(vehicle["brand"], vehicle["vehicleDetails"]
              ["model"]["label"], vehicle["vin"])

    return vin


# https://renault-api.readthedocs.io/en/latest/endpoints.html#vehicle-data-endpoints


def get_charging_status(session, headers, account_id, vin):
    response = session.request(
        "GET",
        KEMERON_URL +
        f"/accounts/{account_id}/kamereon/kca/car-adapter/v2/cars/{vin}/battery-status?country=IT",
        headers=headers,
        data={})
    print(response.json())
    return response.json()


def get_cockpit(session, headers, account_id, vin):
    response = session.request(
        "GET",
        KEMERON_URL +
        f"/accounts/{account_id}/kamereon/kca/car-adapter/v1/cars/{vin}/cockpit?country=IT",
        headers=headers,
        data={})
    print(response.json())
    return response.json()


def get_location(session, headers, account_id, vin):
    response = session.request(
        "GET",
        KEMERON_URL +
        f"/accounts/{account_id}/kamereon/kca/car-adapter/v1/cars/{vin}/location?country=IT",
        headers=headers,
        data={})
    print(response.json())
    return response.json()


offset = 0
last_charge_status = 0
chat_id = CHAT_ID
count = 0


def send_message(msg, parse_mode=""):
    with requests.get(f"https://api.telegram.org/bot{TELEGRAM_KEY}/sendMessage?chat_id={chat_id}&text={msg}&parse_mode={parse_mode}") as req:
        print(req.text)


if __name__ == "__main__":
    session, person_id, account_id, jwt, headers = renault_login()
    vin = get_vin(session, headers, account_id)
    while True:
        print(count, 5 * 60 * 1. /
                        0.5)
        # break
        with requests.get(f"https://api.telegram.org/bot{TELEGRAM_KEY}/getUpdates?offset={offset}") as req:
            if req.status_code != 200:
                print(f"Error {req.text}")
                time.sleep(5)
                continue
            try:
                response = req.json()
                if (count > 15 * 60 * 1. /
                        0.5) or (len(response["result"]) > 0):  # every 15 minutes
                    try:
                        car_state = get_charging_status(session, headers, account_id, vin)
                        car_cockpit = get_cockpit(session, headers, account_id, vin)
                        battery_status = car_state["data"]["attributes"]["batteryLevel"]
                        charging_status = car_state["data"]["attributes"]["chargingStatus"]
                        plug_status = car_state["data"]["attributes"]["plugStatus"]
                        count = 0
                    except Exception as e:
                        print(e)
                        session, person_id, account_id, jwt, headers = renault_login()
                        headers = {
                                        'Content-Type': 'application/vnd.api+json',
                                        'apikey': KAMEREON_API_KEY,
                                        'x-gigya-id_token': jwt,
                                    }
                        vin = get_vin(session, headers, account_id)
                        continue
                    if last_charge_status != charging_status:
                        send_message("Charging status update")
                        send_message(f"Charge: {battery_status}%")
                        last_charge_status = charging_status
                for message in response["result"]:
                    offset = message["update_id"] + 1
                    _chat_id = message["message"]["chat"]["id"]
                    if _chat_id != chat_id:
                        continue
                    text = message["message"]["text"]
                    if "/charge" in text:
                        send_message(f"Charge: {battery_status}%")
                        send_message(("Not " if plug_status ==
                                      0 else " ") + "Plugged in")
                    if "/info" in text:
                        totalMileage = car_cockpit["data"]["attributes"]["totalMileage"]
                        fuelAutonomy = car_cockpit["data"]["attributes"]["fuelAutonomy"]
                        batteryAutonomy = car_state["data"]["attributes"]["batteryAutonomy"]
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
                            f"[Location](https://www.openstreetmap.org/%23map=19/{lat}/{lon})",
                            parse_mode="MarkdownV2")
            except BaseException:
                continue
        count += 1
        time.sleep(0.5)
