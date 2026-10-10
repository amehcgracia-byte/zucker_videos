from flask import Flask
from server import tutorial


def client_at(tmp_path, monkeypatch):
    monkeypatch.setattr(tutorial, 'preference_path', lambda: tmp_path / 'storage.json')
    app = Flask(__name__)
    tutorial.register_tutorial_routes(app)
    return app.test_client()


def test_first_run_and_both_answers_persist_across_app_instances(tmp_path, monkeypatch):
    client = client_at(tmp_path, monkeypatch)
    assert client.get('/api/v1/tutorial').json == {'answer': None}
    for answer in ('yes', 'no'):
        assert client.post('/api/v1/tutorial', json={'answer': answer}).status_code == 200
        reopened = client_at(tmp_path, monkeypatch)
        assert reopened.get('/api/v1/tutorial').json == {'answer': answer}
    assert not list(tmp_path.glob('.tutorial-*'))


def test_invalid_answer_does_not_overwrite_preference(tmp_path, monkeypatch):
    client = client_at(tmp_path, monkeypatch)
    client.post('/api/v1/tutorial', json={'answer': 'no'})
    assert client.post('/api/v1/tutorial', json={'answer': 'maybe'}).status_code == 400
    assert client.get('/api/v1/tutorial').json == {'answer': 'no'}


def test_tutorial_and_menus_are_in_english():
    import re
    from pathlib import Path
    from core.desktop_menu import MENU_ITEMS
    root = Path(__file__).parents[1]
    spanish = re.compile(r"[áéíóúñ¿¡]|\b(Siguiente|Anterior|Terminar|Herramientas|Ayuda|Archivo|Montaje)\b")
    assert not spanish.search((root / "web" / "tutorial.js").read_text(encoding="utf-8"))
    assert not spanish.search(repr(MENU_ITEMS))


def test_new_installation_offers_tutorial_again(tmp_path, monkeypatch):
    monkeypatch.setattr(tutorial, 'installation_id', lambda: 'install-1')
    client = client_at(tmp_path, monkeypatch)
    client.post('/api/v1/tutorial', json={'answer': 'yes'})
    assert client.get('/api/v1/tutorial').json == {'answer': 'yes'}
    monkeypatch.setattr(tutorial, 'installation_id', lambda: 'install-2')
    assert client.get('/api/v1/tutorial').json == {'answer': None}
