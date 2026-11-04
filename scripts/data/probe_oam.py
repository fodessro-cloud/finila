"""Test (GitHub Actions only), round 6: German company announcements. Börse Frankfurt news per ISIN and
EQS News pages for a few large German issuers; prints status and a sample."""
import re, urllib.request

UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "application/json, text/html, */*"}
ISINS = {"SAP": "DE0007164600", "Siemens": "DE0007236101", "Allianz": "DE0008404005", "BASF": "DE000BASF111"}


def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except Exception as e:
        return "ERR " + str(e), None, getattr(e, "read", lambda: b"")()


for name, isin in ISINS.items():
    for url in (f"https://api.boerse-frankfurt.de/v1/data/corporate_news?isin={isin}&limit=5&offset=0",
                f"https://api.boerse-frankfurt.de/v1/data/news?isin={isin}&limit=5",
                f"https://www.boerse-frankfurt.de/equity/{isin}/news"):
        st, ct, b = get(url)
        print("==", name, url, st, ct, len(b), b[:500])
    break

for url in ("https://www.eqs-news.com/company/sap-se/news/", "https://www.eqs-news.com/news/?search=SAP",
            "https://www.eqs-news.com/wp-json/wp/v2/news?per_page=3", "https://www.eqs-news.com/robots.txt",
            "https://www.boerse-frankfurt.de/robots.txt", "https://www.eqs-news.com/terms-of-use/"):
    st, ct, b = get(url)
    t = b.decode("utf-8", "replace")
    print("==", url, st, ct, len(b))
    print(t[:1500] if "robots" in url or "json" in (ct or "") else re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[:800])
