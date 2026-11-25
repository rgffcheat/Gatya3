import os, time, sqlite3, secrets, json, zipfile, threading, urllib.request, urllib.error
from datetime import datetime
from core import DATA_DIR, SECRET, conn, get_setting, set_setting, IST

BK = os.path.join(DATA_DIR, "backups")
_run_lock = threading.Lock()

def interval():
    try: h = int(get_setting("backup_hours") or 1)
    except ValueError: h = 1
    return max(1, min(24, h)) * 3600

def make_backup():
    """Full backup zip: the whole database (users, gateways, orders, settings, mail markers) + the SECRET_KEY
    needed to read stored passwords after moving to a new host."""
    os.makedirs(BK, exist_ok=True)
    stamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
    dbp = os.path.join(BK, f"gateway-{stamp}.db")
    src = conn(); dst = sqlite3.connect(dbp); src.backup(dst); dst.close()
    info = {k: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for k, t in (("users", "users"), ("gateways", "gateways"), ("orders", "orders"))}
    src.close()
    info["created_ist"] = stamp
    zp = os.path.join(BK, f"gateway-backup-{stamp}.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(dbp, "gateway.db"); z.writestr("SECRET_KEY.txt", SECRET); z.writestr("info.json", json.dumps(info))
    os.remove(dbp)
    for f in sorted(x for x in os.listdir(BK) if x.endswith(".zip"))[:-48]:   # keep last 48
        try: os.remove(os.path.join(BK, f))
        except OSError: pass
    return zp, info

def _tg(method, fields, filepath=None):
    tok, chat = get_setting("tg_token").strip(), get_setting("tg_chat").strip()
    if not tok or not chat: return False, "Telegram not set"
    b = "----gw" + secrets.token_hex(8)
    body = b""
    for k, v in dict(chat_id=chat, **fields).items():
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    if filepath:
        body += (f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{os.path.basename(filepath)}"\r\n'
                 f'Content-Type: application/zip\r\n\r\n').encode() + open(filepath, "rb").read() + b"\r\n"
    body += f"--{b}--\r\n".encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/{method}", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        return bool(r.get("ok")), "Sent to Telegram" if r.get("ok") else str(r.get("description"))
    except urllib.error.HTTPError as e:
        try: d = json.loads(e.read().decode()).get("description", "")
        except Exception: d = ""
        return False, f"Telegram error {e.code}: {d or e.reason}"
    except Exception as e:
        return False, f"Telegram error: {str(e)[:100]}"

def send_text(text): return _tg("sendMessage", {"text": text})

def run_backup():
    if not _run_lock.acquire(blocking=False): return False, "A backup is already running."
    try:
        set_setting("last_backup_try", str(time.time()))
        zp, info = make_backup()
        caption = (f"Gateway backup {datetime.now(IST).strftime('%d %b %Y %H:%M')} IST\n"
                   f"Users: {info['users']} | Gateways: {info['gateways']} | Orders: {info['orders']}")
        if not get_setting("tg_token").strip() or not get_setting("tg_chat").strip():
            set_setting("last_backup", str(time.time())); set_setting("last_backup_msg", "Saved on server only (Telegram not set)")
            return True, "Backup saved on server. Set Telegram token and chat ID to receive it."
        ok, msg = _tg("sendDocument", {"caption": caption}, zp)
        if ok: set_setting("last_backup", str(time.time()))
        set_setting("last_backup_msg", ("Saved and sent. " if ok else "Saved locally, Telegram failed: ") + (msg if not ok else "Telegram OK"))
        return ok, msg
    except Exception as e:
        set_setting("last_backup_msg", f"Backup error: {str(e)[:100]}"); return False, f"Backup error: {str(e)[:100]}"
    finally:
        _run_lock.release()

def maybe_auto():
    c = conn()
    has_users = c.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    c.execute("DELETE FROM orders WHERE status='expired' AND expires_ts<?", (time.time() - 14 * 86400,)); c.commit(); c.close()
    if not has_users: return
    t = time.time()
    due = t - float(get_setting("last_backup") or 0) >= interval()
    if due and t - float(get_setting("last_backup_try") or 0) >= 300:      # retry a failed send after 5 min
        threading.Thread(target=run_backup, daemon=True).start()          # never block the order checker
