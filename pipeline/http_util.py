"""Polite HTTP for the Legislative Reference Library (behind Cloudflare, rate-limited).

One request at a time with a minimum gap (PAUSE seconds, default 2), and on 429 or 5xx it backs
off: the server's Retry-After if given, otherwise 1, 2, 4 ... up to 15 minutes. Run only one
downloader against lrl.texas.gov at a time.
"""
import os, time, urllib.error, urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) texas-nominate research crawler (one request at a time)"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({
    "http": os.environ.get("HTTP_PROXY", "http://127.0.0.1:3128"),
    "https": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:3128")}))
_last = [0.0]


def get(url, data=None, tries=10):
    pause = float(os.environ.get("PAUSE", 2.0))
    wait = 60.0
    for attempt in range(tries):
        gap = time.time() - _last[0]
        if gap < pause:
            time.sleep(pause - gap)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with _opener.open(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                retry = e.headers.get("Retry-After")
                delay = float(retry) if retry and retry.isdigit() else wait
                print(f"    {e.code} from server, waiting {delay:.0f}s", flush=True)
                time.sleep(min(delay, 900))
                wait = min(wait * 2, 900)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            time.sleep(min(wait, 300))
            wait = min(wait * 2, 900)
    raise RuntimeError(f"{url}: gave up after {tries} tries")
