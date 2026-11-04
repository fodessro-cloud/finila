"""Test (GitHub Actions only), round 2: details of the readable registers (France AMF info-financiere,
Spain CNMV) and new tries for UK FCA NSM and Euronext."""
import json, urllib.parse, urllib.request
from datetime import date, timedelta

UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "application/json, application/rss+xml, text/html, */*"}
today = date.today()
since = today - timedelta(days=2)


def probe(name, url, data=None, ctype=None, n=1500):
    h = dict(UA)
    if ctype:
        h["Content-Type"] = ctype
    try:
        req = urllib.request.Request(url, data=data, headers=h)
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
            print(f"\n== {name} | {r.status} | {r.headers.get('Content-Type')} | {len(body)} bytes\n{url}\n{body[:n]!r}")
            return body
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        print(f"\n== {name} | ERROR {e}\n{url}\n{body[:400]!r}")


# France: newest records, which fields, which types of information
base = "https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records?"
probe("FR newest", base + urllib.parse.urlencode({"limit": 3, "order_by": "informationdeposee_inf_dat_emt desc"}), n=7000)
probe("FR fields", "https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod", n=9000)
for field in ("informationdeposee_inf_cat_inf", "informationdeposee_inf_typ_inf", "informationdeposee_inf_nat_inf",
              "identificationdiffuseur_idi_cod_dif"):
    probe("FR group " + field, base + urllib.parse.urlencode({
        "select": f"{field}, count(*) as n", "group_by": field, "limit": 40,
        "where": f"informationdeposee_inf_dat_emt >= date'{since}'"}), n=3000)

# Spain: full RSS
probe("ES CNMV OIR", "https://www.cnmv.es/portal/Otra-Informacion-Relevante/RSS.asmx/GetNoticiasCNMV", n=3500)
probe("ES CNMV inside info", "https://www.cnmv.es/portal/Otra-Informacion-Relevante/RSS.asmx/GetNoticiasCNMVHechos", n=1500)

# UK FCA NSM: bodies like the public search page sends
for body in (
    {"from": 0, "size": 3, "sort": "submitted_date", "sortorder": "desc", "criteriaObj": {"criteria": [], "dateCriteria": []}},
    {"from": 0, "size": 3, "sort": "submitted_date", "sortorder": "desc",
     "criteriaObj": {"criteria": [{"name": "latest_flag", "value": "Y"}], "dateCriteria": []}},
):
    probe("UK NSM", "https://api.data.fca.org.uk/search?index=fca-nsm-searchdata", json.dumps(body).encode(), "application/json", n=2500)

# Euronext, EQS
probe("Euronext company news", "https://live.euronext.com/en/products/equities/company-news", n=600)
probe("Euronext rss", "https://live.euronext.com/rss/company-news", n=600)
probe("EQS news list", "https://www.eqs-news.com/news/", n=600)
probe("EQS rss", "https://www.eqs-news.com/feed/rss/", n=600)
