"""Offline verification: CapSolver payloads khớp spec docs.capsolver.com.

Chạy: python3 tests/_verify_capsolver.py
Không cần API key / network — chỉ inspect task dict được build.
"""
import os, sys, json, asyncio, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services.captcha import capsolver as C

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))

# ── 1. env / key presence ────────────────────────────────────────────────
envf = pathlib.Path(__file__).resolve().parents[1] / ".env"
env_has = False
if envf.exists():
    for ln in envf.read_text().splitlines():
        if ln.upper().startswith("CAPSOLVER_API_KEY") and ln.split("=",1)[-1].strip():
            env_has = True
print("CAPSOLVER_API_KEY:", "set(env)" if os.getenv("CAPSOLVER_API_KEY") else ("set(.env)" if env_has else "EMPTY"))

# ── 2. proxy URL parsing → fields format ──────────────────────────────────
f = C._parse_proxy_url("http://user:pass@1.2.3.4:8080")
check("proxy parse fields", f == {"proxyType":"http","proxyAddress":"1.2.3.4","proxyPort":8080,"proxyLogin":"user","proxyPassword":"pass"}, json.dumps(f))
f2 = C._parse_proxy_url("socks5://h:9999")
check("proxy socks5 noauth", f2 == {"proxyType":"socks5","proxyAddress":"h","proxyPort":9999}, json.dumps(f2))
check("proxy datadome format", C._proxy_url_to_capsolver_format("http://u:p@h:7") == "h:7:u:p")
check("proxy bad → None", C._parse_proxy_url("garbage") is None)

# ── 3. _apply_proxy swaps ProxyLess→Task only for proxy-aware types ────────
cs = C.CapSolver(api_key="x", proxy_url="http://u:p@1.1.1.1:80")
t_recap = cs._apply_proxy({"type":"ReCaptchaV2TaskProxyLess","websiteURL":"u","websiteKey":"k"})
check("recaptcha swaps to Task+proxy", t_recap["type"]=="ReCaptchaV2Task" and t_recap.get("proxyAddress")=="1.1.1.1", t_recap["type"])
t_ts = cs._apply_proxy({"type":"AntiTurnstileTaskProxyLess"})
check("turnstile stays ProxyLess (CapSolver rec.)", t_ts["type"]=="AntiTurnstileTaskProxyLess" and "proxyAddress" not in t_ts, t_ts["type"])
t_cf = cs._apply_proxy({"type":"AntiCloudflareTask","websiteURL":"u"})
check("cloudflare merges proxy fields", t_cf.get("proxyAddress")=="1.1.1.1", json.dumps(t_cf))

# proxyless instance must not add proxy fields
cs0 = C.CapSolver(api_key="x")
t0 = cs0._apply_proxy({"type":"ReCaptchaV2TaskProxyLess"})
check("no-proxy keeps ProxyLess", t0["type"]=="ReCaptchaV2TaskProxyLess")

# ── 4. capture actual task dicts by monkeypatching solve/_get_token ───────
captured = {}
async def fake_solve(task):
    captured["task"] = task
    # return a plausible solution per type
    ty = task.get("type","")
    if "Turnstile" in ty: return {"token":"TS"*20,"userAgent":"UA"}
    if "ReCaptcha" in ty: return {"gRecaptchaResponse":"G"*40}
    if "Datadome" in ty: return {"cookie":"datadome=abc; Path=/"}
    if "Cloudflare" in ty: return {"cookies":{"cf_clearance":"x"},"token":"t","userAgent":"UA"}
    return {}
async def fake_get_token(task):
    captured["task"] = task
    return {"gRecaptchaResponse":"G"*40}

async def run():
    cs = C.CapSolver(api_key="x", proxy_url="http://u:p@9.9.9.9:80")
    cs.solve = fake_solve            # type: ignore
    cs._get_token = fake_get_token   # type: ignore

    await cs.solve_turnstile("https://site", "0x4SITEKEY", action="login", cdata="cd")
    t = captured["task"]
    check("Turnstile type", t["type"]=="AntiTurnstileTaskProxyLess")
    check("Turnstile websiteKey", t["websiteKey"]=="0x4SITEKEY")
    check("Turnstile metadata.action/cdata", t.get("metadata")=={"action":"login","cdata":"cd"}, json.dumps(t.get("metadata")))

    await cs.solve_recaptcha_v2("https://s","6Lsitekey", invisible=True)
    t = captured["task"]
    check("reCAPTCHAv2 type+isInvisible", t["type"]=="ReCaptchaV2TaskProxyLess" and t["isInvisible"] is True)

    await cs.solve_recaptcha_v3("https://s","6Lsk", action="submit", min_score=0.7)
    t = captured["task"]
    check("reCAPTCHAv3 pageAction+minScore", t["pageAction"]=="submit" and t["minScore"]==0.7 and t["type"]=="ReCaptchaV3TaskProxyLess")

    await cs.solve_recaptcha_v2("https://s","6Lsk", use_get_token=True)
    t = captured["task"]
    check("getToken path used (v2)", t["type"]=="ReCaptchaV2TaskProxyLess")

    await cs.solve_datadome("https://geo.captcha-delivery.com/captcha/?t=fe", "Mozilla/5.0")
    t = captured["task"]
    check("DataDome type", t["type"]=="DatadomeSliderTask")
    check("DataDome captchaUrl+proxy fmt", t.get("captchaUrl","").endswith("t=fe") and t.get("proxy")=="9.9.9.9:80:u:p", json.dumps({"proxy":t.get("proxy")}))

    await cs.solve_cloudflare_challenge("https://s", user_agent="UA")
    t = captured["task"]
    check("Cloudflare type+proxy merged", t["type"]=="AntiCloudflareTask" and t.get("proxyAddress")=="9.9.9.9")

asyncio.run(run())

# ── 5. report ─────────────────────────────────────────────────────────────
print("\n=== CapSolver payload verification ===")
ok = 0
for name, passed, detail in results:
    print(("PASS" if passed else "FAIL"), "-", name, (("[" + detail + "]") if (detail and not passed) else ""))
    ok += passed
print(f"\n{ok}/{len(results)} checks passed")
sys.exit(0 if ok==len(results) else 1)
