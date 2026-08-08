from pathlib import Path

import pytest
from django.test import Client

from tests.test_cross_role_visual_conformance import _operational_people
from workshops.models import Station

pytestmark = pytest.mark.django_db(transaction=True)


def test_shared_station_ui_anatomy_and_accessibility(settings):
    admin, manager, operator = _operational_people("station-ui")
    Station.objects.create(workshop=admin.workshop, code="ST-001", name="Saw Cell")
    client = Client()
    for viewer in (admin, manager, operator):
        client.force_login(viewer)
        html = client.get("/workshop/stations").content.decode("utf-8")
        for marker in (
            'class="workshop-subnav"',
            'class="filter-toolbar station-filters"',
            'aria-expanded="false"',
            'aria-controls="station-detail-ST-001"',
            "Open station",
        ):
            assert marker in html
    css = (Path(settings.BASE_DIR) / "static/css/foundation.css").read_text(
        encoding="utf-8"
    )
    js = (Path(settings.BASE_DIR) / "static/js/foundation.js").read_text(
        encoding="utf-8"
    )
    assert ".station-filters" in css and "contain: layout paint" in css
    assert "[data-station-disclosure]" in js


def test_admin_station_forms_preserve_the_url_backed_list_context():
    admin, _, _ = _operational_people("station-ui-context")
    Station.objects.create(workshop=admin.workshop, code="ST-001", name="Saw Cell")
    client = Client()
    client.force_login(admin)
    html = client.get(
        "/workshop/stations?q=Saw&lifecycle=active&availability=available&page_size=50"
    ).content.decode("utf-8")
    assert (
        'action="/workshop/stations/create?q=Saw&amp;lifecycle=active&amp;availability=available&amp;capability=&amp;page_size=50&amp;page=1"'
        in html
    )
    assert (
        'action="/workshop/stations/ST-001/edit?q=Saw&amp;lifecycle=active&amp;availability=available&amp;capability=&amp;page_size=50&amp;page=1"'
        in html
    )
