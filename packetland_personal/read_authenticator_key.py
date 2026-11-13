"""
Reads the TOTP key for an account out of Google Authenticator's export QR code.

Google Authenticator never shows an account's key, but its Transfer accounts > Export
accounts screen shows a QR code that carries the keys of the accounts you pick. Give this
script either the text of that QR code (it starts otpauth-migration://) or a photo or
screenshot of it, and it prints each account's key, ready for totp_secret in actionid.json,
along with the code it makes right now so you can check it against the phone.

    python3 read_authenticator_key.py "otpauth-migration://offline?data=..."
    python3 read_authenticator_key.py ~/Desktop/qr.jpg

Reading a photo needs OpenCV (pip install opencv-python). The text needs nothing extra.
Delete the photo when you are done: it holds the same key the phone does.
"""

import base64
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

ALGORITHMS = {0: "unspecified", 1: "SHA1", 2: "SHA256", 3: "SHA512", 4: "MD5"}
DIGITS = {0: "unspecified", 1: 6, 2: 8}
TYPES = {0: "unspecified", 1: "HOTP (counter)", 2: "TOTP (time)"}


def varint(data: bytes, i: int) -> tuple:
    value, shift = 0, 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def fields(data: bytes):
    """Yields (field number, value) for each field of a protobuf message."""
    i = 0
    while i < len(data):
        key, i = varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(data, i)
        elif wire == 2:
            length, i = varint(data, i)
            value, i = data[i:i + length], i + length
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError(f"Unexpected protobuf wire type {wire}")
        yield number, value


def qr_text(source: str) -> str:
    """The QR code's text: the argument itself, or what OpenCV reads off the image."""
    if source.strip().lower().startswith("otpauth"):
        return source.strip()
    path = Path(source).expanduser()
    if not path.exists():
        sys.exit(f"{source} is neither an otpauth-migration:// link nor a file that exists.")
    try:
        import cv2
    except ImportError:
        sys.exit("Reading a photo needs OpenCV: pip install opencv-python, then run this again.")
    image = cv2.imread(str(path))
    if image is None:
        sys.exit(f"Could not open {path} as an image.")
    # Photo Booth and some phone cameras save a mirror image, which no QR reader can read,
    # so each view is also tried flipped, and a small or soft code is also tried enlarged.
    detector = cv2.QRCodeDetector()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    views = [image, cv2.flip(image, 1)]
    for scale in (2, 0.5):
        resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        views += [resized, cv2.flip(resized, 1)]
    text = ""
    for view in views:
        text, _, _ = detector.detectAndDecode(view)
        if text:
            break
    if not text:
        sys.exit("No QR code could be read in that image. Crop it closer to the code, "
                 "or try a sharper photo.")
    return text


def accounts(text: str) -> list:
    if text.lower().startswith("otpauth://"):
        # A plain otpauth:// link (a setup QR code rather than an export) has the key in it.
        secret = re.search(r"[?&]secret=([^&]+)", text)
        name = unquote(text.split("?")[0].rsplit("/", 1)[-1])
        if not secret:
            sys.exit("That otpauth:// link has no secret= in it.")
        return [{"name": name, "issuer": "", "key": unquote(secret.group(1)).upper(),
                 "algorithm": "SHA1", "digits": 6, "type": "TOTP (time)"}]

    data = re.search(r"[?&]data=([^&]+)", text)
    if not data:
        sys.exit("That text has no data= part. Copy the whole otpauth-migration:// link.")
    raw = unquote(data.group(1)).replace(" ", "+")
    payload = base64.b64decode(raw + "=" * (-len(raw) % 4))

    found = []
    for number, value in fields(payload):
        if number != 1:
            continue
        entry = {"name": "", "issuer": "", "key": "", "algorithm": "SHA1", "digits": 6,
                 "type": "TOTP (time)"}
        for field, item in fields(value):
            if field == 1:
                entry["key"] = base64.b32encode(item).decode().rstrip("=")
            elif field == 2:
                entry["name"] = item.decode("utf-8", "replace")
            elif field == 3:
                entry["issuer"] = item.decode("utf-8", "replace")
            elif field == 4:
                entry["algorithm"] = ALGORITHMS.get(item, item)
            elif field == 5:
                entry["digits"] = DIGITS.get(item, item)
            elif field == 6:
                entry["type"] = TYPES.get(item, item)
        found.append(entry)
    return found


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    found = accounts(qr_text(sys.argv[1]))
    if not found:
        sys.exit("The QR code held no accounts.")
    try:
        import pyotp
    except ImportError:
        pyotp = None

    left = 30 - int(time.time()) % 30
    for entry in found:
        label = f"{entry['issuer']}: {entry['name']}" if entry["issuer"] else entry["name"]
        print(f"\n{label}")
        print(f"  totp_secret: {entry['key']}")
        if pyotp:
            print(f"  code right now: {pyotp.TOTP(entry['key']).now()}  ({left}s left)")
        odd = [f"{what} is {entry[what]}" for what, usual in
               [("algorithm", "SHA1"), ("digits", 6), ("type", "TOTP (time)")]
               if entry[what] not in (usual, "unspecified")]
        if odd:
            print("  Note: " + "; ".join(odd) + ". The login script assumes SHA1, 6 digits "
                  "and time-based codes, so this key may not work with it as is.")


if __name__ == "__main__":
    main()
