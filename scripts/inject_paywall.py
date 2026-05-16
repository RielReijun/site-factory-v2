"""
inject_paywall.py — Injecteer paywall-overlay in de gegenereerde homepage.

Alle interne links (nav, footer, CTA-knoppen) krijgen data-paywall="true".
Een click op zo'n link toont een modal met blurry backdrop:
  "Interesse? kost €300 as is. We komen graag in gesprek."

Werking:
  1. Lees out/index.html
  2. Voeg data-paywall toe aan alle interne <a> links
  3. Injecteer paywall-CSS in <head>
  4. Injecteer paywall-modal + JS voor </body>
  5. Sla op (in-place)
"""
import argparse
import base64
import os
import re
from pathlib import Path


# ── Paywall CSS ──────────────────────────────────────────────────────────────

PAYWALL_STYLE = """<style id="paywall-style">
#paywall-overlay {
  display: none;
  position: fixed;
  inset: 0;
  z-index: 99999;
  backdrop-filter: blur(14px) saturate(1.4);
  -webkit-backdrop-filter: blur(14px) saturate(1.4);
  background: rgba(0, 0, 0, 0.52);
  align-items: center;
  justify-content: center;
}
#paywall-overlay.pw-active { display: flex; }
#paywall-modal {
  background: #ffffff;
  border-radius: 1.25rem;
  padding: 2.75rem 2.25rem 2rem;
  max-width: 440px;
  width: 90vw;
  text-align: center;
  box-shadow: 0 32px 80px rgba(0, 0, 0, 0.28);
  animation: pw-in 0.22s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  position: relative;
}
@keyframes pw-in {
  from { transform: scale(0.88) translateY(16px); opacity: 0; }
  to   { transform: scale(1)    translateY(0);    opacity: 1; }
}
#paywall-modal h2 {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0 0 0.6rem;
  color: #111827;
  letter-spacing: -0.02em;
}
#paywall-modal p {
  color: #6b7280;
  margin: 0 0 1.75rem;
  line-height: 1.65;
  font-size: 1rem;
}
#paywall-modal strong { color: #111827; }
#paywall-close {
  display: inline-block;
  background: #111827;
  color: #ffffff;
  border: none;
  border-radius: 0.6rem;
  padding: 0.65rem 2rem;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
  letter-spacing: 0.01em;
}
#paywall-close:hover { background: #374151; transform: translateY(-1px); }
#paywall-close:active { transform: translateY(0); }
#paywall-dismiss {
  display: block;
  margin-top: 1rem;
  font-size: 0.82rem;
  color: #9ca3af;
  cursor: pointer;
  background: none;
  border: none;
  text-decoration: underline;
}
#paywall-dismiss:hover { color: #6b7280; }
</style>
"""

# ── Paywall HTML + JS ────────────────────────────────────────────────────────

PAYWALL_BODY = """<script id="paywall-script">
(function () {
  "use strict";

  /* Maak overlay dynamisch aan — overleeft React hydration */
  function ensureOverlay() {
    var el = document.getElementById("paywall-overlay");
    if (el) return el;

    var style = document.createElement("style");
    style.textContent = [
      "#paywall-overlay{display:none;position:fixed;inset:0;z-index:99999;",
      "backdrop-filter:blur(14px) saturate(1.4);",
      "-webkit-backdrop-filter:blur(14px) saturate(1.4);",
      "background:rgba(0,0,0,.52);align-items:center;justify-content:center}",
      "#paywall-overlay.pw-active{display:flex}",
      "#paywall-modal{background:#fff;border-radius:1.25rem;padding:2.75rem 2.25rem 2rem;",
      "max-width:440px;width:90vw;text-align:center;",
      "box-shadow:0 32px 80px rgba(0,0,0,.28);",
      "animation:pw-in .22s cubic-bezier(.34,1.56,.64,1) both}",
      "@keyframes pw-in{from{transform:scale(.88) translateY(16px);opacity:0}",
      "to{transform:scale(1) translateY(0);opacity:1}}",
      "#paywall-modal h2{font-size:1.5rem;font-weight:700;margin:0 0 .6rem;color:#111}",
      "#paywall-modal p{color:#6b7280;margin:0 0 1.75rem;line-height:1.65}",
      "#paywall-modal strong{color:#111}",
      "#pw-close{display:inline-block;background:#111;color:#fff;border:none;",
      "border-radius:.6rem;padding:.65rem 2rem;font-size:.95rem;font-weight:600;",
      "cursor:pointer;transition:background .15s}",
      "#pw-close:hover{background:#374151}",
      "#pw-dismiss{display:block;margin-top:1rem;font-size:.82rem;color:#9ca3af;",
      "cursor:pointer;background:none;border:none;text-decoration:underline}"
    ].join("");
    document.head.appendChild(style);

    el = document.createElement("div");
    el.id = "paywall-overlay";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    var phone = document.querySelector("meta[name=agency-phone]");
    var email = document.querySelector("meta[name=agency-email]");
    var name  = document.querySelector("meta[name=agency-name]");
    var pVal  = phone ? phone.content : "";
    var eVal  = email ? email.content : "";
    var nVal  = name  ? name.content  : "";
    var contactHtml = "";
    if (pVal) contactHtml += '<a href="tel:' + pVal + '" style="display:block;color:#6366f1;font-weight:600;text-decoration:none;margin-bottom:4px">&#128222; ' + pVal + '</a>';
    if (eVal) contactHtml += '<a href="mailto:' + eVal + '" style="display:block;color:#6366f1;font-weight:600;text-decoration:none">&#9993; ' + eVal + '</a>';
    el.innerHTML = [
      '<div id="paywall-modal">',
      '<h2>Interesse?</h2>',
      '<p>Deze website is speciaal voor u gemaakt.<br>',
      'Prijs: <strong>&euro;300</strong> inclusief overdracht van alle bestanden.</p>',
      contactHtml ? '<div style="margin-bottom:1.5rem">' + contactHtml + '</div>' : '',
      '<button id="pw-close">Sluiten</button>',
      '</div>'
    ].join("");
    document.body.appendChild(el);

    el.addEventListener("click", function (e) { if (e.target === el) close(); });
    el.querySelector("#pw-close").addEventListener("click", close);
    el.querySelector("#pw-dismiss").addEventListener("click", close);
    return el;
  }

  function open(e) {
    if (e) e.preventDefault();
    var overlay = ensureOverlay();
    overlay.classList.add("pw-active");
    document.body.style.overflow = "hidden";
  }

  function close() {
    var overlay = document.getElementById("paywall-overlay");
    if (overlay) overlay.classList.remove("pw-active");
    document.body.style.overflow = "";
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });

  var SKIP = ["http://", "https://", "mailto:", "tel:", "javascript:", "//",
              "/_next", "/assets", "/logo", "/favicon", "/sitemap", "/robots"];
  var SKIP_EXACT = ["/", "/index.html", "", "index.html"];

  function shouldPaywall(href) {
    if (!href) return false;
    href = href.trim();
    if (SKIP_EXACT.indexOf(href) !== -1) return false;
    for (var i = 0; i < SKIP.length; i++) {
      if (href.indexOf(SKIP[i]) === 0) return false;
    }
    return true;
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest("a");
    if (!a) return;
    var href = a.getAttribute("href") || "";
    if (shouldPaywall(href)) open(e);
  }, true);
})();
</script>
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_internal_link(href: str) -> bool:
    """Return True als href een interne link is die paywalled moet worden."""
    href = href.strip()
    if not href:
        return False
    # Bare "#" → Claude-placeholder voor subpagina-link: paywallen
    if href == "#":
        return True
    # Anchors met sectie-ID, externe links, assets → overslaan
    skip_prefixes = ("#", "tel:", "mailto:", "javascript:", "http://", "https://", "//",
                     "/_next", "/assets", "/logo", "/favicon", "/sitemap", "/robots")
    if any(href.startswith(s) for s in skip_prefixes):
        return False
    # Homepage zelf niet paywallen
    if href in ("/", "/index.html", "", "index.html"):
        return False
    return True


def _add_paywall_attrs(html: str) -> tuple[str, int]:
    """Voeg data-paywall='true' toe aan alle interne <a> tags. Geeft (html, count)."""
    count = 0

    def replace_link(m: re.Match) -> str:
        nonlocal count
        tag = m.group(0)
        href_match = re.search(r'href=["\']([^"\']*)["\']', tag)
        if not href_match:
            return tag
        if _is_internal_link(href_match.group(1)) and "data-paywall" not in tag:
            tag = tag.replace("<a ", '<a data-paywall="true" ', 1)
            count += 1
        return tag

    html = re.sub(r"<a\s[^>]*>", replace_link, html, flags=re.DOTALL)
    return html, count


def _inject_style(html: str) -> str:
    """Injecteer paywall-CSS vlak voor </head>."""
    if "</head>" in html:
        return html.replace("</head>", PAYWALL_STYLE + "</head>", 1)
    return PAYWALL_STYLE + html


def _inject_body(html: str) -> str:
    """Injecteer paywall-modal + script vlak voor </body>."""
    if "</body>" in html:
        return html.replace("</body>", PAYWALL_BODY + "</body>", 1)
    return html + PAYWALL_BODY


def _img_to_b64(path: Path) -> str | None:
    """Lees een afbeelding en geef base64 data-URI terug."""
    if not path or not path.exists():
        return None
    try:
        data = path.read_bytes()
        ext  = path.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


def _build_comparison_script(original_b64: str | None, new_b64: str | None,
                              agency_phone: str, agency_email: str) -> str:
    """Bouw het paywall-script — met voor/na slider als beide screenshots beschikbaar zijn."""
    has_comparison = bool(original_b64 and new_b64)
    orig = original_b64 or ""
    neww = new_b64 or ""
    modal_width = "720" if has_comparison else "440"

    return f"""<script id="paywall-script">
(function(){{
  "use strict";
  var ORIG="{orig}",NEWW="{neww}";
  var HAS_CMP=!!(ORIG&&NEWW);

  function buildSlider(){{
    if(!HAS_CMP)return'';
    /* Na (NEWW) = basis, bepaalt container-hoogte.
       Voor (ORIG) = absolute overlay, clip-path knipt van rechts. */
    return '<div id="pw-cmp">'
      +'<img id="pw-after"  src="'+NEWW+'" />'
      +'<img id="pw-before" src="'+ORIG+'" />'
      +'<div id="pw-divline"></div>'
      +'<span class="pw-lbl pw-lbl-l">Voor</span>'
      +'<span class="pw-lbl pw-lbl-r">Na</span>'
      +'<input type="range" min="0" max="100" value="50" id="pw-range" />'
      +'</div>';
  }}

  function initSlider(){{
    var before=document.getElementById("pw-before");
    var divline=document.getElementById("pw-divline");
    var inp=document.getElementById("pw-range");
    if(!before||!inp)return;
    /* clip-path knipt voor-afbeelding van rechts — geen JS-breedte nodig */
    before.style.clipPath="inset(0 50% 0 0)";
    divline.style.left="50%";
    inp.oninput=function(){{
      var pos=parseInt(inp.value);
      before.style.clipPath="inset(0 "+(100-pos)+"% 0 0)";
      divline.style.left=pos+"%";
    }};
  }}

  function ensureOverlay(){{
    var el=document.getElementById("paywall-overlay");
    if(el)return el;

    var s=document.createElement("style");
    s.textContent=
      "#paywall-overlay{{display:none;position:fixed;inset:0;z-index:2147483647;"
      +"backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);"
      +"background:rgba(0,0,0,.65);align-items:center;justify-content:center;padding:16px}}"
      +"#paywall-overlay.pw-active{{display:flex}}"
      +"#pw-modal{{background:#fff;border-radius:1.25rem;padding:1.75rem 1.75rem 1.5rem;"
      +"max-width:{modal_width}px;width:100%;text-align:center;"
      +"box-shadow:0 40px 100px rgba(0,0,0,.35);animation:pw-in .25s cubic-bezier(.34,1.56,.64,1) both;"
      +"max-height:92vh;overflow-y:auto}}"
      +"@keyframes pw-in{{from{{transform:scale(.88);opacity:0}}to{{transform:scale(1);opacity:1}}}}"
      +"#pw-modal h2{{font-size:1.3rem;font-weight:700;margin:0 0 .3rem;color:#111}}"
      +"#pw-modal .pw-sub{{color:#6b7280;margin:0 0 .75rem;font-size:.875rem}}"
      /* Slider */
      +"#pw-cmp{{position:relative;width:100%;line-height:0;overflow:hidden;"
      +"border-radius:.75rem;margin-bottom:.5rem;box-shadow:0 4px 20px rgba(0,0,0,.15)}}"
      +"#pw-after{{display:block;width:100%;height:auto;pointer-events:none}}"
      +"#pw-before{{position:absolute;top:0;left:0;width:100%;height:100%;"
      +"object-fit:cover;object-position:top left;pointer-events:none}}"
      +"#pw-divline{{position:absolute;top:0;bottom:0;left:50%;width:3px;"
      +"background:#fff;transform:translateX(-50%);pointer-events:none;"
      +"box-shadow:0 0 10px rgba(0,0,0,.4)}}"
      +".pw-lbl{{position:absolute;top:10px;font-size:11px;font-weight:700;"
      +"color:#fff;background:rgba(0,0,0,.55);padding:3px 10px;border-radius:20px;"
      +"pointer-events:none;letter-spacing:.04em;line-height:1.4}}"
      +".pw-lbl-l{{left:10px}}"
      +".pw-lbl-r{{right:10px;background:rgba(34,197,94,.85)}}"
      +"#pw-range{{position:absolute;inset:0;width:100%;height:100%;"
      +"-webkit-appearance:none;-moz-appearance:none;"
      +"background:transparent;cursor:ew-resize;margin:0;opacity:0}}"
      /* Contact */
      +"#pw-contact{{margin:.5rem 0 1rem}}"
      +"#pw-contact a{{display:block;color:#6366f1;font-weight:600;text-decoration:none;"
      +"margin-bottom:.3rem;font-size:.95rem}}"
      +"#pw-close{{background:#111;color:#fff;border:none;border-radius:.5rem;"
      +"padding:.6rem 2rem;font-size:.9rem;font-weight:600;cursor:pointer;transition:background .15s}}"
      +"#pw-close:hover{{background:#374151}}";
    document.head.appendChild(s);

    el=document.createElement("div");
    el.id="paywall-overlay";
    el.setAttribute("role","dialog");

    var tel="{agency_phone}",em="{agency_email}";
    var ct='<div id="pw-contact">'
      +(tel?'<a href="tel:'+tel+'">&#128222; '+tel+'</a>':'')
      +(em? '<a href="mailto:'+em+'">&#9993; '+em+'</a>':'')
      +'</div>';

    el.innerHTML='<div id="pw-modal">'
      +'<h2>Interesse?</h2>'
      +'<p class="pw-sub">Prijs: <strong>&euro;300</strong> inclusief overdracht</p>'
      +buildSlider()
      +ct
      +'<button id="pw-close">Sluiten</button>'
      +'</div>';
    document.body.appendChild(el);

    el.addEventListener("click",function(e){{if(e.target===el)close();}});
    document.getElementById("pw-close").addEventListener("click",close);
    initSlider();

    return el;
  }}

  function open(e){{if(e)e.preventDefault();ensureOverlay().classList.add("pw-active");document.body.style.overflow="hidden";}}
  function close(){{var o=document.getElementById("paywall-overlay");if(o)o.classList.remove("pw-active");document.body.style.overflow="";}}
  document.addEventListener("keydown",function(e){{if(e.key==="Escape")close();}});

  var SKIP=["http://","https://","mailto:","tel:","javascript:","//","/_next","/assets","/logo","/favicon","/sitemap","/robots"];
  var SKIP_EXACT=["/","/index.html","","index.html"];
  function shouldPaywall(h){{
    if(!h)return false;h=h.trim();
    if(SKIP_EXACT.indexOf(h)!==-1)return false;
    for(var i=0;i<SKIP.length;i++){{if(h.indexOf(SKIP[i])===0)return false;}}
    return true;
  }}
  document.addEventListener("click",function(e){{
    var a=e.target.closest("a");
    if(!a)return;
    if(shouldPaywall(a.getAttribute("href")||""))open(e);
  }},true);
}})();
</script>"""


def process_file(html_path: Path,
                 original_screenshot: Path | None = None,
                 new_screenshot: Path | None = None) -> int:
    """Verwerk één HTML-bestand. Geeft het aantal gepaywallde links terug."""
    html = html_path.read_text(encoding="utf-8", errors="ignore")

    # Idempotent: verwijder bestaande injectie zodat we altijd vers injecteren
    html = re.sub(r'<script id="paywall-script">[\s\S]*?</script>\s*', '', html)

    agency_phone = os.getenv("AGENCY_PHONE", "")
    agency_email = os.getenv("AGENCY_EMAIL", "")

    orig_b64 = _img_to_b64(original_screenshot)
    new_b64  = _img_to_b64(new_screenshot)

    if orig_b64 and new_b64:
        print(f"[INFO] Voor/na vergelijking: origineel={original_screenshot.name} nieuw={new_screenshot.name}")
    elif original_screenshot or new_screenshot:
        print(f"[WARN] Slechts één screenshot beschikbaar — geen vergelijking")

    script = _build_comparison_script(orig_b64, new_b64, agency_phone, agency_email)

    html, count = _add_paywall_attrs(html)
    html = html.replace("</body>", script + "\n</body>", 1)
    html_path.write_text(html, encoding="utf-8")
    return count


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Injecteer paywall-overlay in de gegenereerde homepage."
    )
    parser.add_argument("--site-dir",            required=True)
    parser.add_argument("--original-screenshot", default="",
                        help="Pad naar screenshot van de originele site")
    parser.add_argument("--new-screenshot",      default="",
                        help="Pad naar screenshot van de gegenereerde site")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    index    = site_dir / "index.html"

    if not index.exists():
        print(f"[FAIL] index.html niet gevonden: {index}")
        raise SystemExit(1)

    orig = Path(args.original_screenshot) if args.original_screenshot else None
    new  = Path(args.new_screenshot)      if args.new_screenshot      else None

    count = process_file(index, original_screenshot=orig, new_screenshot=new)
    print(f"[OK]  Paywall geïnjecteerd: {count} links paywalled in {index.name}")


if __name__ == "__main__":
    main()
