"""A presenter that only SAYS it presents a domain type.

This is the fixture for the model-link clause. Its claim to presenting a model
is the word "domain" in this docstring and nothing else: it imports no inner
type, annotates none, and returns whatever it is handed. A bare substring over
raw source is cleared by this file; reading the IMPORTS is not.
"""


class WidgetView:
    @staticmethod
    def single(widget: dict[str, object]) -> dict[str, object]:
        return widget
