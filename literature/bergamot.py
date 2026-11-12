import hashlib
import json
import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from threading import Lock

import requests

CDN = "https://firefox-settings-attachments.cdn.mozilla.net/main-workspace/"
GLUE = ("https://raw.githubusercontent.com/mozilla/gecko-dev/"
        "5836a062726f715fda621338a17b51aff30d0a8c/"
        "toolkit/components/translations/bergamot-translator/bergamot-translator.js")
ASSETS = {
    "bergamot-translator.js": (
        GLUE, "972fa0450146fe06f4006a2ba56e8c4834403e27873cfe47fb3ba85aa7b9b082", 99827,
    ),
    "bergamot-translator.wasm": (
        CDN + "translations-wasm/05082c31-aee8-4249-9e01-c1865afd7520.wasm",
        "a3a89d9ad0a4ed8f27bf3e403701b23f5709816f6376438503f2fa5b0182c2dc", 4960506,
    ),
    "model.ruen.intgemm.alphas.bin": (
        CDN + "translations-models/466ccac8-f8c7-4fee-81ee-477a1f817f1c.bin",
        "b1d85c13cfbb05e1d326dd6f0fb5ef270a2011b547450260f96567a93f446c94", 17141051,
    ),
    "lex.50.50.ruen.s2t.bin": (
        CDN + "translations-models/fe5156a8-ab98-4597-84c5-f1248eda4770.bin",
        "f654693577505fd38b1f3d220cdd4ffffbb45afb900a60cf751f0724eadc74e0", 4483844,
    ),
    "vocab.ruen.spm": (
        CDN + "translations-models/40916383-0537-4869-992a-6606ee0cfb97.spm",
        "93bdc941b16e523695c319f74778bca9fd8b75a25ad75020cdc98aef74cdc0fc", 905257,
    ),
    "model.enru.intgemm.alphas.bin": (
        CDN + "translations-models/0ed2208e-01d5-4251-9fcd-5ad2167ba456.bin",
        "01db60f75a54ef39c617cbdfdd78759a6a216576efa9a9088da8a6433d90af77", 17141051,
    ),
    "lex.50.50.enru.s2t.bin": (
        CDN + "translations-models/0a9c0bc3-19a7-4b0a-9f9c-f8494a0e59f9.bin",
        "3587f93c10c1d457f874c2ba7ff4d5e2686fdaf7f3fd179cfefeacab3c6990b9", 2768468,
    ),
    "vocab.enru.spm": (
        CDN + "translations-models/d410fe5f-3f5d-4af6-9841-ca55beab47f7.spm",
        "56ee63e14e8cb926c394242adc3ed7cc602644c3d33058cff2ce2959d52a6258", 904455,
    ),
}
TRANSLATION_LOCK = Lock()
LOGGER = logging.getLogger(__name__)


class TranslationError(Exception):
    pass


def download_asset(target: Path, url: str, checksum: str, size: int):
    if target.is_file() and target.stat().st_size == size:
        with target.open("rb") as cached:
            if hashlib.file_digest(cached, "sha256").hexdigest() == checksum:
                return

    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            received = 0
            digest = hashlib.sha256()
            with requests.get(url, stream=True, timeout=(6, 30)) as response:
                response.raise_for_status()
                for chunk in response.iter_content(256 * 1024):
                    received += len(chunk)
                    if received > size:
                        raise ValueError("Bergamot asset exceeded the expected size")
                    digest.update(chunk)
                    output.write(chunk)
            if received != size or digest.hexdigest() != checksum:
                raise ValueError("Bergamot asset checksum or size mismatch")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def asset_directory() -> Path:
    default = Path(gettempdir()) / "pimiim-bergamot-ru-en-v1"
    directory = Path(os.environ.get("PIMIIM_MODEL_CACHE", default)).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(download_asset, directory / name, *asset)
            for name, asset in ASSETS.items()
        ]
        for future in futures:
            future.result()
    return directory


class BergamotTranslator:
    def translate(self, text: str) -> tuple[str, str]:
        node = shutil.which("node") or shutil.which("nodejs")
        if node is None:
            raise TranslationError("Для локального перевода нужен Node.js.")

        with TRANSLATION_LOCK:
            try:
                directory = asset_directory()
                process = subprocess.run(
                    [node, str(Path(__file__).with_name("bergamot.cjs")), str(directory)],
                    input=json.dumps({"text": text}, ensure_ascii=False),
                    encoding="utf-8", capture_output=True, timeout=25, check=True,
                )
                payload = json.loads(process.stdout)
                translated, back_translation = payload["translated"], payload["backTranslation"]
                if not isinstance(translated, str) or not isinstance(back_translation, str):
                    raise ValueError("Bergamot returned invalid translations")
            except (OSError, subprocess.SubprocessError, requests.RequestException,
                    ValueError, KeyError) as error:
                LOGGER.warning("Bergamot translation failed: %s", type(error).__name__)
                if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                    LOGGER.warning("Bergamot worker error: %s", error.stderr.strip()[:300])
                raise TranslationError(
                    "Локальный перевод недоступен. Можно продолжить с английским запросом."
                ) from None
        return translated, back_translation
