# ussd_voltage_mongo.py
from flask import Flask, request, make_response
from pymongo import MongoClient
import requests
import os
from dotenv import load_dotenv
import base64

load_dotenv()
app = Flask(__name__)

# --- Voltage / LND Config ---
VOLTAGE_REST_URL = os.getenv("VOLTAGE_REST_URL")  # e.g. https://xxx.voltage.cloud:8080
VOLTAGE_MACAROON = os.getenv("VOLTAGE_MACAROON")  # hex or base64 macaroon

HEADERS = {
    "Grpc-Metadata-macaroon": VOLTAGE_MACAROON,
    "Content-Type": "application/json"
}

# --- MongoDB ---
MONGO_URI = os.getenv("MONGODB_URI")
client = MongoClient(MONGO_URI)
db = client["ussd_wallet"]
users_col = db["users"]
session_col = db["sessions"]

# ---------------- HELPERS ----------------

def lnd_get(path):
    try:
        r = requests.get(VOLTAGE_REST_URL + path, headers=HEADERS, timeout=10, verify=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def lnd_post(path, data):
    try:
        r = requests.post(VOLTAGE_REST_URL + path, headers=HEADERS, json=data, timeout=10, verify=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def ussd_response(text):
    resp = make_response(text, 200)
    resp.headers["Content-Type"] = "text/plain"
    return resp


def get_or_create_user(phone):
    user = users_col.find_one({"phone": phone})
    if not user:
        user = {
            "phone": phone,
            "balance_sats": 0
        }
        users_col.insert_one(user)
    return user

# -------------- USSD ROUTE --------------

@app.route("/ussd", methods=["POST"])
def ussd():
    session_id = request.values.get("sessionId")
    text = request.values.get("text", "").strip()

    session = session_col.find_one({"sessionId": session_id})
    if not session:
        session = {"sessionId": session_id, "stage": "welcome"}
        session_col.insert_one(session)

    stage = session["stage"]

    # 1. Welcome
    if stage == "welcome":
        if text == "":
            session_col.update_one({"sessionId": session_id}, {"$set": {"stage": "enter_phone"}})
            return ussd_response("CON Welcome to Bitcoin Brave ⚡\nEnter your phone number:")

    # 2. Phone Entry
    if stage == "enter_phone":
        phone = text
        if not phone.isdigit() or len(phone) < 10:
            return ussd_response("CON Invalid number. Enter phone number:")

        user = get_or_create_user(phone)
        session_col.update_one(
            {"sessionId": session_id},
            {"$set": {"stage": "main_menu", "phone": phone}}
        )

        menu = (
            "CON Bitcoin Brave ⚡\n"
            "1. Check Balance\n"
            "2. Receive sats\n"
            "3. Send sats\n"
            "4. Exit"
        )
        return ussd_response(menu)

    # 3. Main Menu
    if stage == "main_menu":
        phone = session["phone"]
        user = users_col.find_one({"phone": phone})

        if text == "1":
            return ussd_response(f"END Wallet Balance: {user.get('balance_sats',0)} sats")

        elif text == "2":
            session_col.update_one({"sessionId": session_id}, {"$set": {"stage": "enter_receive_amount"}})
            return ussd_response("CON Enter amount in sats:")

        elif text == "3":
            session_col.update_one({"sessionId": session_id}, {"$set": {"stage": "enter_invoice"}})
            return ussd_response("CON Enter Lightning Invoice:")

        else:
            return ussd_response("END Goodbye 👋🏾")

    # 4. CREATE INVOICE (RECEIVE)
    if stage == "enter_receive_amount":
        try:
            amount = int(text)
        except:
            return ussd_response("CON Invalid amount. Enter sats:")

        invoice = lnd_post("/v1/invoices", {"value": amount})

        if "error" in invoice:
            return ussd_response(f"END Error generating invoice")

        payment_request = invoice.get("payment_request")

        session_col.update_one({"sessionId": session_id}, {"$set": {"stage": "main_menu"}})
        return ussd_response(f"END Invoice:\n{payment_request}")

    # 5. PAY INVOICE (SEND)
    if stage == "enter_invoice":
        payment = lnd_post("/v1/channels/transactions", {"payment_request": text})

        session_col.update_one({"sessionId": session_id}, {"$set": {"stage": "main_menu"}})

        if "error" in payment:
            return ussd_response("END Payment failed")

        return ussd_response("END Payment Sent ✅")

    return ussd_response("END Unexpected error")


if __name__ == "__main__":
    app.run(port=5000, host="0.0.0.0")
