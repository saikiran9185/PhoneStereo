#!/usr/bin/env python3
"""PhoneStereo Mac app: find the phone, pair, start depth.

1. Finds the phone over Bonjour (the PhoneStereo app advertises _phonestereo._tcp after Start).
2. Asks for the 4-digit pairing code once (shown in the phone app) and remembers it per phone.
3. Lets you pick Live depth or the Sign Check game and opens it, streaming straight over Wi-Fi.

Run: .venv/bin/python connect.py   (or open PhoneStereo.app, made by ./make_mac_app.sh)
"""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.expanduser("~/.config/phonestereo/pairing.json")
SERVICE = "_phonestereo._tcp"
PY = os.path.join(HERE, ".venv", "bin", "python")


def osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def alert(msg):
    osa(f'display dialog "{msg}" with title "PhoneStereo" buttons {{"OK"}} default button 1')


def ask(prompt, default=""):
    rc, out = osa(f'text returned of (display dialog "{prompt}" default answer "{default}" '
                  f'with title "PhoneStereo")')
    return out if rc == 0 else None


def run_for(cmd, seconds):
    """Run a never-ending dns-sd command for a few seconds and return its output."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    time.sleep(seconds)
    p.terminate()
    return p.communicate()[0]


def discover(timeout=5):
    """-> list of (name, ip, port) for phones running the PhoneStereo app."""
    found = []
    out = run_for(["dns-sd", "-B", SERVICE, "local."], timeout)
    names = []
    for line in out.splitlines():
        m = re.search(r"\sAdd\s+\d+\s+\d+\s+local\.\s+" + re.escape(SERVICE) + r"\.\s+(.*)$", line)
        if m and m.group(1).strip() not in names:
            names.append(m.group(1).strip())
    for name in names:
        res = run_for(["dns-sd", "-L", name, SERVICE, "local."], 2)
        m = re.search(r"can be reached at (\S+?)\.?:(\d+)", res)
        if not m:
            continue
        host, port = m.group(1), int(m.group(2))
        addr = run_for(["dns-sd", "-G", "v4", host], 2)
        ips = re.findall(r"\s(\d+\.\d+\.\d+\.\d+)\s", addr)
        if ips:
            found.append((name, ips[-1], port))
    return found


def check(ip, port, code):
    """'ok' if the phone accepts the code, 'code' if it refuses it, else an error string."""
    try:
        s = socket.create_connection((ip, port), timeout=5)
        s.settimeout(8)
        s.sendall(f"PS {code}\n".encode())
        first = s.recv(8, socket.MSG_WAITALL)
        s.close()
        if first.startswith(b"ERR"):
            return "code"
        if len(first) == 8 and struct.unpack(">I", first[:4])[0] == 0x50535431:
            return "ok"
        return "no answer from the phone app"
    except OSError as e:
        return str(e)


def load():
    try:
        with open(CONF) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(conf):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF, "w") as f:
        json.dump(conf, f, indent=2)
    os.chmod(CONF, 0o600)


def main():
    conf = load()
    print("looking for the phone ...", flush=True)
    phones = []
    for _ in range(3):
        phones = discover()
        if phones:
            break
    if phones:
        name, ip, port = phones[0]
        if len(phones) > 1:
            labels = ", ".join(f'"{n}"' for n, _, _ in phones)
            rc, pick = osa(f'choose from list {{{labels}}} with title "PhoneStereo" with prompt "Which phone?"')
            if rc != 0 or pick == "false":
                return
            name, ip, port = next(p for p in phones if p[0] == pick)
    else:
        ip = None
        while True:
            ip = ask("Phone not found automatically. In the phone app tap Start, then type the phone IP "
                     "shown under the code (like 172.16.163.67), NOT the 4-digit code:", conf.get("last_ip", ""))
            if not ip:
                return
            ip = ip.strip().split(":")[0]
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip):
                break
        name, port = "phone@" + ip, 27183
    print(f"found {name} at {ip}:{port}", flush=True)

    code = conf.get("codes", {}).get(name)
    for attempt in range(3):
        if not code:
            code = ask(f"Pairing code for {name} (the 4 digits in the phone app):")
            if not code:
                return
        r = check(ip, port, code.strip())
        if r == "ok":
            break
        if r == "code":
            code = None
            continue
        alert(f"Can't reach the phone app: {r}")
        return
    else:
        alert("Wrong pairing code 3 times.")
        return
    conf.setdefault("codes", {})[name] = code
    conf["last_ip"] = ip
    save(conf)

    rc, mode = osa('choose from list {"Live depth", "Sign Check game (3 cameras)"} with title "PhoneStereo" '
                   'with prompt "Paired with the phone. Open:" default items {"Live depth"}')
    if rc != 0 or mode == "false":
        return
    script = "viewer.py" if mode.startswith("Live") else "signs.py"
    os.chdir(HERE)
    os.execv(PY, [PY, script, "--host", ip, "--token", code])


if __name__ == "__main__":
    main()
