from app.worker import celery_app, ping


def test_ping_task_runs():
    # apply() runs the task in-process, so no Redis is needed.
    assert ping.apply().get() == "pong"


def test_ping_task_is_registered():
    assert "lexintake.ping" in celery_app.tasks
