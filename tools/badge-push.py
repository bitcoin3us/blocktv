#!/usr/bin/env python3
"""Push files to an MPOS board over a console that cannot take big copies.

    tools/badge-push.py PORT DEST_DIR FILE [FILE ...]
    tools/badge-push.py /dev/cu.usbmodem5B901591931 /apps/org.zaptv.blocktv blocktv.py

`mpremote fs cp` truncates anything over ~20 KB on the ESP32-P4's CH343
console (no flow control; the board drains its console in Python), and
back-to-back execs hit the same wall. This sends 1 KB base64 chunks, one
`mpremote exec` each, and the BOARD verifies each chunk's sha256 before
appending it -- so a corrupted chunk is simply resent -- with a short pause
between chunks and a resume from whatever verified prefix is already on
the board. About 1 KB/s; 120 KB in under two minutes with no retries.

Never combine this with anything that runs asyncio on the board from the
console: relaunch the app afterwards through AppManager.start_app instead.
"""
import base64, hashlib, os, re, subprocess, sys, time

CHUNK = 1024          # starting chunk size
MIN_CHUNK = 128       # halved down to this while the board keeps failing
PACE = 0.3


def ex(port, code, timeout=60):
    try:
        r = subprocess.run(["mpremote", "connect", port, "exec", code],
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).replace("\r", "")
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>"


def wait_ready(port, limit=900):
    """Block until the board answers a trivial exec, or `limit` seconds.

    The console shares the board's event loop, so anything the OS does
    that blocks the loop for a while (the launcher's periodic network
    checks, on the P4) makes every chunk fail until it is over. Waiting
    for a reply costs nothing; hammering retries during the pause burns
    the retry budget for no reason."""
    t0 = time.time()
    while time.time() - t0 < limit:
        if re.search(r"READY= 1", ex(port, "print('READY= 1')", timeout=20)):
            return True
        time.sleep(10)
    return False


def ex_match(port, code, pattern, tries=3):
    for _ in range(tries):
        m = re.search(pattern, ex(port, code))
        if m:
            return m
        time.sleep(1.5)
    return None


def verified_prefix(port, path, data):
    """How many bytes of `data` the board provably holds at `path`: the
    file's size if its content hashes like the same-length prefix of
    `data`, else 0. Every recovery goes through this rather than trusting
    a counter: an unverified truncate whose offset got mangled on the way
    over once left the file shorter than the counter said, and nothing
    could ever be appended again."""
    total = len(data)
    m = ex_match(port, "import os, hashlib, binascii\ntry:\n    n = os.stat(%r)[6]\nexcept OSError:\n    n = 0\n"
                       "h = ''\nif n:\n    f = open(%r, 'rb'); h = binascii.hexlify(hashlib.sha256(f.read()).digest()).decode()[:12]; f.close()\n"
                       "print('P= %%d %%s' %% (n, h))" % (path, path), r"P= (\d+) ?([0-9a-f]{12})?")
    if not m:
        return None
    n, h = int(m.group(1)), m.group(2) or ""
    if n == 0:
        return 0
    if n <= total and hashlib.sha256(data[:n]).hexdigest()[:12] == h:
        return n
    return 0


def push(port, dest, name):
    data = open(name, "rb").read()
    total = len(data)
    path = dest.rstrip("/") + "/" + os.path.basename(name)
    want = hashlib.sha256(data).hexdigest()[:12]
    sent = verified_prefix(port, path, data)
    if sent is None:
        print("%s: board not answering" % name)
        return False
    if sent == total:
        print("%s: already on the board and identical" % name)
        return True
    if sent == 0 and not ex_match(port, "open(%r, 'wb').close(); print('T= 0')" % path, r"T= 0"):
        print("%s: could not create the file" % name)
        return False
    print("%s: %d bytes, starting at %d" % (name, total, sent))
    t0 = time.time()
    retries = consecutive = 0
    size = CHUNK
    while sent < total:
        chunk = data[sent:sent + size]
        h = hashlib.sha256(chunk).hexdigest()[:12]
        code = ("import binascii, hashlib, os\nc = binascii.a2b_base64(%r)\n"
                "h = binascii.hexlify(hashlib.sha256(c).digest()).decode()[:12]\n"
                "if h == %r and os.stat(%r)[6] == %d:\n    f = open(%r, 'ab'); f.write(c); f.close()\n"
                "print('C= %%s %%d' %% (h, os.stat(%r)[6]))" % (base64.b64encode(chunk), h, path, sent, path, path))
        ok = False
        for _ in range(10):
            m = re.search(r"C= ([0-9a-f]{12}) (\d+)", ex(port, code))
            if m and m.group(1) == h and int(m.group(2)) == sent + len(chunk):
                ok, consecutive = True, 0
                break
            retries += 1
            consecutive += 1
            # Re-sync to what the board provably holds; never truncate blind.
            have = verified_prefix(port, path, data)
            if have is None:
                pass
            elif have == sent + len(chunk):
                ok, consecutive = True, 0       # it landed; only the reply was lost
                break
            elif have != sent:
                sent = have                     # continue from the proven prefix
                break
            if consecutive >= 2:
                # A starved console still answers a tiny ping but drops
                # bigger pastes: shrink the chunk, and wait if even the
                # ping goes unanswered.
                if size > MIN_CHUNK:
                    size = max(MIN_CHUNK, size // 2)
                    print("  chunk at %d failing; trying %d-byte chunks" % (sent, size))
                    break
                print("  board not answering at %d; waiting for it..." % sent)
                if not wait_ready(port):
                    break
            time.sleep(2)
        if not ok:
            if consecutive < 12:
                continue                # resynced or resized: go again from `sent`
            print("%s: chunk at %d never verified after %d retries" % (name, sent, retries))
            return False
        sent += len(chunk)
        if size < CHUNK and consecutive == 0 and sent % (4 * size) == 0:
            size = min(CHUNK, size * 2)    # things calmed down: grow back
        time.sleep(PACE)
    m = ex_match(port, "import hashlib, binascii, os\nprint('H= %%s %%d' %% (binascii.hexlify(hashlib.sha256(open(%r, 'rb').read()).digest()).decode()[:12], os.stat(%r)[6]))" % (path, path), r"H= ([0-9a-f]{12}) (\d+)")
    good = bool(m) and m.group(1) == want and int(m.group(2)) == total
    print("%s: %s (%d retries, %.0fs)" % (name, "VERIFIED" if good else "MISMATCH", retries, time.time() - t0))
    return good


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    port, dest, files = argv[1], argv[2], argv[3:]
    return 0 if all(push(port, dest, f) for f in files) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
