import atexit
import logging
import random
import time
from pathlib import Path
from urllib.parse import urlencode

try:
    from curl_cffi import requests as cf_requests
except ImportError:
    cf_requests = None

logger = logging.getLogger(__name__)

class SoFifaHttpClient:
    def __init__(
        self,
        min_delay: float = 1.2,
        max_delay: float = 2.6,
        max_retries: int = 5,
        timeout: int = 30,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = cf_requests.Session(impersonate="chrome")
        self.session.headers.update(
            {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://sofifa.com/",
            }
        )

def get_html(self, url: str, params: dict | None = None) -> str:
        full_url = f"{url}?{urlencode(params)}" if params else url
        min_delay = getattr(self, "min_delay", 1.2)
        max_delay = getattr(self, "max_delay", 2.4)
        timeout = getattr(self, "timeout", 25)
        max_retries = getattr(self, "max_retries", 5)

        if getattr(self, "_browser_fallback_active", False):
            time.sleep(random.uniform(min_delay, max_delay))
            return self._browser_fb.fetch(full_url)

        profiles = ("chrome", "safari", "edge")
        for attempt in range(1, max_retries + 1):
            if cf_requests is not None:
                profile = profiles[(attempt - 1) % len(profiles)]
                sess_map = getattr(self, "_cf_sessions", {})
                if profile not in sess_map:
                    # Nao sobrescreva User-Agent manualmente para preservar consistencia com sec-ch-ua
                    s = cf_requests.Session(impersonate=profile)
                    s.headers.update({
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://sofifa.com/",
                    })
                    sess_map[profile] = s
                    self._cf_sessions = sess_map

                try:
                    time.sleep(random.uniform(min_delay, max_delay))
                    resp = sess_map[profile].get(full_url, timeout=timeout)
                    if resp.status_code == 200 and not _is_cf_blocked(resp.status_code, resp.text):
                        return resp.text
                    logger.warning(
                        "HTTP %s ou desafio Cloudflare em %s (perfil=%s, tentativa %d/%d).",
                        resp.status_code, full_url, profile, attempt, max_retries,
                    )
                except Exception as exc:
                    logger.warning("Erro HTTP (%s) em %s: %s", profile, full_url, exc)

            # Se 2 perfis TLS diferentes foram barrados por 403, ativa a sessao real do navegador
            if attempt >= 2:
                logger.info("Ativando fallback via navegador nativo (Edge/Chrome) para contornar Turnstile...")
                self._browser_fb = getattr(self, "_browser_fb", None) or _BrowserSessionFallback()
                self._browser_fallback_active = True
                return self._browser_fb.fetch(full_url)

            time.sleep(2.0 * attempt)

        raise RuntimeError(f"Falha ao coletar URL após {max_retries} tentativas: {full_url}")

def _is_cf_blocked(status_code: int, html: str) -> bool:
    if status_code in (403, 429, 503):
        return True
    head = html[:4096].lower()
    return (
        "<title>just a moment" in head
        or "<title>um momento" in head
        or ("cf-chl-" in head and "<table" not in html.lower())
    )


class _BrowserSessionFallback:
    """Usa o Edge/Chrome nativo do Windows com perfil salvo quando o IP exige Turnstile."""

    def __init__(self, profile_dir: str = "data/.sofifa_browser") -> None:
        self.profile_dir = Path(profile_dir)
        self._pw = None
        self._ctx = None
        self._page = None

    def _ensure_started(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        for channel in ("msedge", "chrome"):
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    channel=channel,
                    headless=False,
                    no_viewport=True,
                    ignore_default_args=["--enable-automation"],
                    args=["--disable-blink-features=AutomationControlled"],
                )
                break
            except Exception:
                continue
        if self._ctx is None:
            raise RuntimeError("Nao foi possivel iniciar o Microsoft Edge ou Google Chrome.")

        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        atexit.register(self.close)

    def fetch(self, full_url: str, timeout_sec: float = 90.0) -> str:
        self._ensure_started()
        assert self._page is not None
        self._page.goto(full_url, wait_until="domcontentloaded", timeout=45000)

        deadline = time.time() + timeout_sec
        notified = False
        while time.time() < deadline:
            try:
                title = self._page.title().lower()
                html = self._page.content()
            except Exception:
                # Ignora transicao de pagina no exato instante do redirecionamento do Cloudflare
                time.sleep(0.5)
                continue

            if (
                "moment" not in title
                and "momento" not in title
                and ("<table" in html.lower() or "sofifa" in title)
            ):
                return html

            if not notified:
                logger.info(
                    "Aguardando liberacao do Cloudflare na janela do navegador "
                    "(marque a caixa 'Verificar se voce e humano' se aparecer)..."
                )
                notified = True
            time.sleep(1.0)

        raise RuntimeError(f"Tempo esgotado aguardando desbloqueio no navegador: {full_url}")

    def close(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = None
        self._pw = None
        self._page = None