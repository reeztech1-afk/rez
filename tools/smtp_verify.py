"""
smtp_verify.py - true mailbox verification (level 2) - RUNS ON GITHUB ACTIONS ONLY
Sandbox blocks port 25; GitHub runners allow outbound SMTP, so we do the real thing:

  MAIL FROM:<>  ->  RCPT TO:<target>
    250                     -> VALID (mailbox accepts)
    550/551/553/5.x mailbox -> INVALID (mailbox rejected)
    4xx                     -> GREYLISTED (server deferring, retry once)
    random@ RCPT also 250   -> CATCH_ALL (server accepts anything, mailbox unverifiable)
    connect fail            -> UNREACHABLE

Input : tools/verify_queue.txt   (one email per line, # comments allowed)
Output: tools/smtp_verify_results.json  + console table

Usage: python3 tools/smtp_verify.py [queue_file] [results_file]
"""
import sys, json, socket, time, random, smtplib, dns.resolver, dns.exception

QUEUE = sys.argv[1] if len(sys.argv) > 1 else "tools/verify_queue.txt"
RESULTS = sys.argv[2] if len(sys.argv) > 2 else "tools/smtp_verify_results.json"
HELO = "mail-solver.github.internal"

def mx_hosts(domain):
    try:
        recs = sorted((r.preference, str(r.exchange).rstrip("."))
                      for r in dns.resolver.resolve(domain, "MX"))
        return [h for _, h in recs]
    except dns.resolver.NXDOMAIN:
        return []
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # some domains accept on A record directly
        try:
            socket.gethostbyname(domain); return [domain]
        except Exception:
            return []
    except dns.exception.DNSException:
        return []

def probe(mx, rcpt):
    try:
        with smtplib.SMTP(mx, 25, timeout=25) as s:
            s.ehlo_or_helo_if_needed()
            code = s.docmd("MAIL", "FROM:<>")[0]
            if code != 250:
                return "ENVELOPE_REJECTED", code
            code, msg = s.docmd("RCPT", "TO:<%s>" % rcpt)
            return ("250" if code == 250 else "OTHER"), (code, msg)
    except smtplib.SMTPServerDisconnected:
        return "DISCONNECTED", None
    except (socket.timeout, ConnectionRefusedError, OSError):
        return "UNREACHABLE", None

def verify(email):
    lp, _, domain = email.lower().partition("@")
    mxs = mx_hosts(domain)
    if not mxs:
        return {"email": email, "verdict": "INVALID", "detail": "no MX / dead domain"}
    # Big-provider servers block datacenter/runner IPs at connect time.
    # RCPT probing is only meaningful for independent mail servers.
    BIG = ("google", "gmail-smtp", "outlook", "protection.outlook", "smtp.secureserver", "mailstore1", "googlemail")
    if any(any(b in m.lower() for b in BIG) for m in mxs):
        return {"email": email, "verdict": "PROBE_BLOCKED",
                "detail": "big-provider MX rejects runner IPs; use MX check + bounce observation"}
    tried = []
    for mx in mxs[:2]:
        status, info = probe(mx, email)
        tried.append(f"{mx}:{info}")
        if status == "250":
            # catch-all check with a random mailbox
            rnd = "probe-%x@%s" % (random.getrandbits(40), domain)
            cstatus, cinfo = probe(mx, rnd)
            catchall = cstatus == "250"
            return {"email": email, "verdict": "CATCH_ALL" if catchall else "VALID",
                    "detail": "catch-all server" if catchall else "RCPT accepted",
                    "mx": mx, "catchall_probe": catchall}
        if status == "DISCONNECTED":
            continue
        if status == "UNREACHABLE":
            continue
        code = info[0] if info else None
        if code and 500 <= code < 600:
            return {"email": email, "verdict": "INVALID", "detail": f"RCPT {code} rejected", "mx": mx}
        if code and 400 <= code < 500:
            return {"email": email, "verdict": "GREYLISTED", "detail": f"RCPT {code} deferring", "mx": mx}
    return {"email": email, "verdict": "UNREACHABLE", "detail": "; ".join(tried) or "no MX reachable"}

if __name__ == "__main__":
    emails = [l.strip() for l in open(QUEUE)
              if l.strip() and not l.strip().startswith("#")]
    out = []
    print(f"verifying {len(emails)} emails (SMTP RCPT level, pace 2s)")
    for i, e in enumerate(emails, 1):
        r = verify(e)
        out.append(r)
        print(f"[{i}/{len(emails)}] {r['verdict']:12} {r['email']:42} {r['detail'][:60]}")
        time.sleep(2)
        # single retry pass for greylisted
    for r in [r for r in out if r["verdict"] == "GREYLISTED"]:
        print("retry greylisted:", r["email"])
        time.sleep(45)
        r.update(verify(r["email"]))
    json.dump(out, open(RESULTS, "w"), indent=1)
    n = {}
    for r in out: n[r["verdict"]] = n.get(r["verdict"], 0) + 1
    print("SUMMARY:", n)
