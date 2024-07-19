import time
import requests
import os

# https://muscatoxblog.blogspot.com/2019/07/delving-into-renaults-new-api.html
API_KEY = '3_js8th3jdmCWV86fKR3SXQWvXGKbHoWFv8NAgRbH7FnIBsi_XvCpN_rtLcI07uNuq'
KAMEREON_API_KEY = "YjkKtHmGfaceeuExUDKGxrLZGGvtVS0J"
BASE_URL = 'https://accounts.eu1.gigya.com'
KEMERON_URL = 'https://api-wired-prod-1-euw1.wrd-aws.com/commerce/v1'
TELEGRAM_KEY = ""
url = "https://accounts.eu1.gigya.com/accounts.login"


def renault_login():
    session = requests.Session()

    payload = {'ApiKey': API_KEY,
               'loginID': '',
               'password': ''}
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
    print(response.text)
    sessionCookie = response.json()["sessionInfo"]["cookieValue"]
    print(sessionCookie)

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

    response = requests.request(
        "GET",
        KEMERON_URL +
        f"/persons/{person_id}?country=IT",
        headers=headers,
        data=payload)

    print(response.json()["accounts"][0])
    account_id = response.json()["accounts"][0]["accountId"]

    return session, person_id, account_id, jwt, headers


def get_vin():
    payload = {}
    headers = {
        'Content-Type': 'application/vnd.api+json',
        'apikey': KAMEREON_API_KEY,
        'x-gigya-id_token': jwt,
    }

    response = requests.request(
        "GET",
        KEMERON_URL +
        f"/accounts/{account_id}/vehicles?country=IT",
        headers=headers,
        data=payload)
    vin = response.json()["vehicleLinks"][0]['vin']

    for vehicle in response.json()["vehicleLinks"]:
        print(vehicle["brand"], vehicle["vehicleDetails"]
              ["model"]["label"], vehicle["vin"])

    return vin


session, person_id, account_id, jwt, headers = renault_login()
vin = get_vin()

# https://renault-api.readthedocs.io/en/latest/endpoints.html#vehicle-data-endpoints


def get_charging_status():
    response = requests.request(
        "GET",
        KEMERON_URL +
        f"/accounts/{account_id}/kamereon/kca/car-adapter/v2/cars/{vin}/battery-status?country=IT",
        headers=headers,
        data={})
    print(response.json())
    return response.json()


def get_cockpit():
    response = requests.request(
        "GET",
        KEMERON_URL +
        f"/accounts/{account_id}/kamereon/kca/car-adapter/v1/cars/{vin}/cockpit?country=IT",
        headers=headers,
        data={})
    print(response.json())
    return response.json()


offset = 0
chat_id = -4217685043


def send_message(msg):
    with requests.get(f"https://api.telegram.org/bot{TELEGRAM_KEY}/sendMessage?chat_id={chat_id}&text={msg}") as req:
        print(req.text)


while True:
    with requests.get(f"https://api.telegram.org/bot{TELEGRAM_KEY}/getUpdates?offset={offset}") as req:
        if req.status_code != 200:
            print("Error")
            time.spleep(5)
            continue
        try:
            response = req.json()
            for message in response["result"]:
                offset = message["update_id"] + 1
                _chat_id = message["message"]["chat"]["id"]
                if _chat_id != chat_id:
                    continue
                text = message["message"]["text"]
                if text == "/charge":
                    car_state = get_charging_status()
                    battery_status = car_state["data"]["attributes"]["batteryLevel"]
                    plug_status = car_state["data"]["attributes"]["plugStatus"]
                    send_message(f"Charge: {battery_status}%")
                    send_message(
                        ("Not " if plug_status == 0 else " ") + "Plugged")
        except BaseException:
            continue
    time.sleep(1)
