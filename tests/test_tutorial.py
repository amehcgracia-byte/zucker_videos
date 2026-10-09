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
