"""Setup-gate middleware stub.

S1 stub: always passes. A2 replaces with real Workshop existence check.
"""


class SetupGateMiddleware:
    """Placeholder middleware; always allows passage."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response
