"""No-op translation stub (English only)."""

LANGUAGE_NAMES = {"EN": "English"}


class Translator:
    def __init__(self, lang="EN"):
        self.lang = lang

    def __call__(self, english_text, **kwargs):
        return english_text.format(**kwargs) if kwargs else english_text

    def set_language(self, lang):
        if lang in LANGUAGE_NAMES:
            self.lang = lang


_tr = Translator()
