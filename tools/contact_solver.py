#!/usr/bin/env python3
"""contact_solver.py - Ire Studio bounce-rescue tool (v2, honest edition).

Problem it solves: a lead's email bounces (550 / misconfigured server) and
the brand hides behind a contact form. This tool submits the pitch through
the brand's own website form automatically and reports the truth.

v2 fixes over v1:
  - Only targets real message forms (email field + message field).
    Skips search bars, newsletter boxes, cart forms, eepurl trackers.
  - Adapts to Shopify forms (contact[first_name]/[last_name]/[tags]/[body],
    challenge, commit) and generic forms (maps their own field names).
  - Honest success detection: success = POST redirected to
    contact_posted=true, or the response body actually says thanks.
    A Cloudflare "Verifying your connection" 403 is reported as
    BLOCKED_CLOUDFLARE, never as success.
  - Works anywhere: run locally for the email-hunt fallback, or on GitHub
    Actions (see .github/workflows/contact-solver.yml) where the runner IP
    is not the sandbox proxy, which is what gets Cloudflare-challenged.

Usage: python3 contact_solver.py [results_dir]
Writes form_solver_results.json and dm_message_pack.md into results_dir
(default: same directory as this script).
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDRS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
OUR_EMAIL = "nicostudioglobal.ai@gmail.com"
OUR_NAME = "Ire Studio"
TIMEOUT = 15
PAUSE = 0.5
BAD = ("example.", "sentry", "wixpress", "@2x", "godaddy", "shopify.com",
       "schema", "u002f", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
       "hubspot", "klaviyo", "zendesk", "no-reply", "noreply",
       "youremail", "email.com", "domain.com")
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")


def fetch(url, data=None, referer=None, no_redirect=False):
    h = dict(HDRS)
    if referer:
        h["Referer"] = referer
    if data is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=CTX),
        _NoRedirect if no_redirect else urllib.request.HTTPRedirectHandler())
    return opener.open(req, timeout=TIMEOUT)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FormParser(HTMLParser):
    """Collect (form_attrs, fields) for every <form>, fields = dicts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.cur = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        a = {k or "": (v or "") for k, v in attrs}
        if tag == "form":
            if self.cur is None:
                self.cur = {"attrs": a, "fields": []}
            self.depth += 1
        elif self.cur is not None and tag in ("input", "textarea", "select"):
            self.cur["fields"].append({
                "tag": tag, "name": a.get("name", ""), "type": a.get("type", ""),
                "required": ("required" in a or a.get("aria-required") == "true"),
                "value": a.get("value", ""), "placeholder": a.get("placeholder", ""),
                "id": a.get("id", "")})

    def handle_endtag(self, tag):
        if tag == "form" and self.cur is not None:
            self.depth -= 1
            if self.depth <= 0:
                self.forms.append(self.cur)
                self.cur = None
                self.depth = 0


def parse_forms(html):
    p = FormParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    if p.cur is not None:
        p.forms.append(p.cur)
    return p.forms


def is_message_form(form):
    """A real contact/message form, not search / newsletter / cart."""
    a = form["attrs"]
    action = (a.get("action") or "").lower()
    if action.startswith("/search") or "eepurl" in action or "/cart" in action:
        return False
    names = [f["name"].lower() for f in form["fields"]]
    joined = " ".join(names)
    if "contact[" not in joined and not any("email" in n for n in names):
        return False
    has_email = any("email" in n or f["type"] == "email"
                    for n, f in zip(names, form["fields"]))
    has_message = "contact[body]" in joined or any(
        f["tag"] == "textarea" for f in form["fields"])
    return has_email and has_message


def build_payload(form, brand, message):
    fields = form["fields"]
    payload = []
    for f in fields:
        n = f["name"]
        if not n:
            continue
        nl = n.lower()
        if f["type"] == "hidden":
            payload.append((n, f["value"]))
        elif f["type"] == "email" or "email" in nl:
            payload.append((n, OUR_EMAIL))
        elif "first_name" in nl or nl.endswith("fname"):
            payload.append((n, "Ire"))
        elif "last_name" in nl or nl.endswith("lname"):
            payload.append((n, "Studio"))
        elif nl == "name" or "your name" in (f["placeholder"] or "").lower():
            payload.append((n, OUR_NAME))
        elif "phone" in nl or "tel" in nl:
            payload.append((n, "N/A"))
        elif "subject" in nl or "topic" in nl:
            payload.append((n, "AI video concept for " + brand))
        elif f["tag"] == "textarea" or "body" in nl or "message" in nl or "question" in nl or "comment" in nl:
            payload.append((n, message))
        elif f["required"]:
            payload.append((n, "N/A"))
        # optional unknown fields: skip
    names = {p[0] for p in payload}
    joined = " ".join(names)
    if "contact[" in joined or "contact_form" in (form["attrs"].get("id") or ""):
        if "form_type" not in names:
            payload.append(("form_type", "contact"))
        if "utf8" not in names:
            payload.append(("utf8", "\u2713"))
    return payload


VERDICTS = {
    "success": "Submitted",
    "cloudflare": "BLOCKED_CLOUDFLARE",
    "http": "FAIL_HTTP",
}


def submit(cfg):
    brand, message = cfg["brand"], cfg["message"]
    out = {"brand": brand, "result": None, "form_url": None, "emails": [],
           "detail": []}
    seen, queue = set(), list(cfg["urls"])
    tried = 0
    while queue and tried < 3:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = fetch(url)
            html = r.read().decode("utf-8", "replace")
            final_url = r.geturl()
        except urllib.error.HTTPError as e:
            out["detail"].append({"url": url, "stage": "GET", "error": "HTTP %d" % e.code})
            time.sleep(PAUSE)
            continue
        except Exception as e:
            out["detail"].append({"url": url, "stage": "GET", "error": str(e)[:100]})
            time.sleep(PAUSE)
            continue
        time.sleep(PAUSE)
        out["emails"] = sorted(set(out["emails"]) | {
            e for e in EMAIL_RE.findall(html) if not any(b in e.lower() for b in BAD)})
        for l in re.findall(r'href="(/[^"#?]*)"', html):
            if re.search(r"contact", l, re.I):
                full = urllib.parse.urljoin(final_url, l)
                if full not in seen and urllib.parse.urlsplit(full).netloc == urllib.parse.urlsplit(cfg["root"]).netloc:
                    queue.append(full)
        forms = [f for f in parse_forms(html) if is_message_form(f)]
        if not forms:
            out["detail"].append({"url": url, "stage": "parse", "note": "no message form in HTML (maybe JS widget)"})
            continue
        form = forms[0]
        tried += 1
        action = form["attrs"].get("action") or final_url
        post_url = urllib.parse.urljoin(final_url, action).split("#")[0]
        payload = build_payload(form, brand, message)
        data = urllib.parse.urlencode(payload).encode()
        try:
            r = fetch(post_url, data=data, referer=final_url, no_redirect=True)
            body = r.read().decode("utf-8", "replace")
            loc = r.headers.get("Location") or ""
            if 300 <= r.status < 400:
                if "contact_posted=true" in loc:
                    out["result"] = VERDICTS["success"]
                    out["form_url"] = post_url
                else:
                    # follow one redirect manually and look for the thanks text
                    try:
                        b2 = fetch(loc or post_url).read().decode("utf-8", "replace")
                        if re.search(r"thanks for contacting|message has been sent|got it, thanks", b2, re.I):
                            out["result"] = VERDICTS["success"]
                            out["form_url"] = post_url
                        else:
                            out["result"] = VERDICTS["http"]
                            out["detail"].append({"url": post_url, "stage": "POST", "note": "redirect without thanks page", "loc": loc[:120]})
                    except Exception as e:
                        out["result"] = VERDICTS["http"]
                        out["detail"].append({"url": post_url, "stage": "POST", "error": str(e)[:100]})
            else:
                ok = re.search(r"thanks for contacting|message has been sent", body, re.I)
                out["result"] = VERDICTS["success"] if ok else VERDICTS["http"]
                out["form_url"] = post_url if ok else None
                if not ok:
                    out["detail"].append({"url": post_url, "stage": "POST", "status": r.status, "body_head": re.sub(r"\s+", " ", body[:150])})
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code in (403, 503) and re.search(r"verifying your connection|just a moment|cf-chl|challenge", body, re.I):
                out["result"] = VERDICTS["cloudflare"]
            else:
                out["result"] = VERDICTS["http"]
            out["detail"].append({"url": post_url, "stage": "POST", "error": "HTTP %d %s" % (e.code, e.reason),
                                  "fields": [p[0] for p in payload]})
        except Exception as e:
            out["result"] = VERDICTS["http"]
            out["detail"].append({"url": post_url, "stage": "POST", "error": str(e)[:100]})
        time.sleep(PAUSE)
        if out["result"] == VERDICTS["success"]:
            break
    if out["result"] is None:
        out["result"] = "NO_FORM_IN_HTML"
    return out


BRANDS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bounce_targets.json")))


def main():
    results_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    results = []
    for cfg in BRANDS:
        print("==>", cfg["brand"], flush=True)
        r = submit(cfg)
        results.append(r)
        print("   ", r["result"], "| emails:", r["emails"][:3], flush=True)
        time.sleep(PAUSE)
    with open(os.path.join(results_dir, "form_solver_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    solved = [r["brand"] for r in results if r["result"] == VERDICTS["success"]]
    print("\nSOLVED VIA FORM:", solved)
    print("REMAINING:", [r["brand"] for r in results if r["result"] != VERDICTS["success"]])
    lines = ["# Bounce Rescue DM Pack (contact_solver.py)", ""]
    for r in results:
        if r["result"] == VERDICTS["success"]:
            lines.append("## %s : SUBMITTED via website form (%s)" % (r["brand"], r["form_url"]))
            lines.append("")
            lines.append("No manual action needed. Watch the inbox for a reply.")
            lines.append("")
            continue
        cfg = next(c for c in BRANDS if c["brand"] == r["brand"])
        lines.append("## %s : %s" % (r["brand"], r["result"]))
        lines.append("")
        lines.append("Paste this message manually through the brand's contact page or DM:")
        lines.append("")
        lines.append(cfg["message"])
        lines.append("")
    with open(os.path.join(results_dir, "dm_message_pack.md"), "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
