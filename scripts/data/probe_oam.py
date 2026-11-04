"""Test (GitHub Actions only): which official registers of company announcements outside the Nordics can be
read automatically (UK FCA NSM, France AMF/DILA, Germany EQS, Spain CNMV, Italy 1Info, Euronext)."""
import json, urllib.request
from datetime import date, timedelta

UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "application/json, application/rss+xml, text/html, */*"}
today = date.today()
since = today - timedelta(days=3)


def probe(name, url, data=None, ctype=None):
    h = dict(UA)
    if ctype:
        h["Content-Type"] = ctype
    try:
        req = urllib.request.Request(url, data=data, headers=h)
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
            print(f"\n== {name} | {r.status} | {r.headers.get('Content-Type')} | {len(body)} bytes\n{url}\n{body[:900]!r}")
    except Exception as e:
        print(f"\n== {name} | ERROR {e}\n{url}")


nsm = {"from": 0, "size": 5, "sortorder": "desc", "sort": "submitted_date",
       "criteriaObj": {"criteria": [], "dateCriteria": [{"name": "submitted_date", "value": {"from": since.isoformat() + "T00:00:00Z", "to": today.isoformat() + "T23:59:59Z"}}]}}
probe("UK FCA NSM search", "https://api.data.fca.org.uk/search?index=fca-nsm-searchdata", json.dumps(nsm).encode(), "application/json")
probe("FR DILA open data", "https://echanges.dila.gouv.fr/OPENDATA/INFOFINANCIERE/")
probe("FR info-financiere", "https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records?limit=3")
probe("FR AMF BDIF", "https://bdif.amf-france.org/back/api/v1/informations?typesInformation=PRIIPS&from=0&size=3")
probe("DE EQS news RSS", "https://www.eqs-news.com/feed/")
probe("DE EQS ad-hoc RSS", "https://www.eqs-news.com/news/adhoc/feed/")
probe("DE DGAP RSS", "https://www.dgap.de/dgap/News/?newsType=ADHOC&format=rss")
probe("ES CNMV RSS", "https://www.cnmv.es/portal/RSS/RSS.aspx")
probe("ES CNMV OIR", "https://www.cnmv.es/portal/Otra-Informacion-Relevante/RSS.asmx/GetNoticiasCNMV")
probe("IT 1Info", "https://www.1info.it/PORTALE1INFO/")
probe("Euronext press releases", "https://live.euronext.com/en/listview/company-press-release")
probe("Euronext press releases json", "https://live.euronext.com/en/ajax/getPressReleases")
