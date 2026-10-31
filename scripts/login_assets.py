"""Fixed UI dependencies, shared by login preloads and static-file handling.

Only assets explicitly referenced by the bundled destination pages qualify.
HTML, uploads, training media and API responses never use this path.
"""
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

STATIC = Path(__file__).parent / 'static'
PAGES = {'/admin': 'admin-console.html', '/atendimento': 'account-home.html',
         '/em-breve': 'account-home.html', '/senha': 'account-password.html'}


class Dependencies(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        kind = {'script': 'script', 'img': 'image'}.get(tag)
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            kind = 'style'
        url = attrs.get('src' if tag != 'link' else 'href', '')
        parsed = urlsplit(url)
        if not kind or parsed.scheme or parsed.netloc or not parsed.path.startswith('/tech-assets/'):
            return
        relative = parsed.path.removeprefix('/tech-assets/')
        # Do not accidentally include generated/private media or page templates.
        if '/' in relative or relative != Path(relative).name:
            return
        if Path(relative).suffix not in {'.css', '.js', '.png', '.svg', '.webp'}:
            return
        if (STATIC / relative).is_file():
            item = {'href': url, 'as': kind}
            if item not in self.assets:
                self.assets.append(item)


@lru_cache(maxsize=4)
def preload_assets(destination: str) -> tuple[dict, ...]:
    name = PAGES.get(destination)
    if not name:
        return ()
    parser = Dependencies()
    parser.feed((STATIC / name).read_text(encoding='utf-8'))
    return tuple(parser.assets)


@lru_cache(maxsize=1)
def presentation_paths() -> frozenset[str]:
    return frozenset(urlsplit(asset['href']).path
                     for destination in PAGES for asset in preload_assets(destination))
