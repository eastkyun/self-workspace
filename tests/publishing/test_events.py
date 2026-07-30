from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from publishing.events import (
    AfterCommitEventDispatcher,
    PublicationCreated,
)


def test_events_are_frozen_and_contain_no_markdown() -> None:
    event = PublicationCreated(1, 13, "manual", "install", "2026-07-30T00:00:00Z")

    assert "markdown" not in event.__dataclass_fields__
    with pytest.raises(FrozenInstanceError):
        event.slug = "changed"  # type: ignore[misc]


def test_subscriber_failure_is_isolated(caplog: pytest.LogCaptureFixture) -> None:
    received = []

    def broken(event) -> None:
        raise RuntimeError("subscriber down")

    dispatcher = AfterCommitEventDispatcher((broken, received.append))
    event = PublicationCreated(1, 13, "manual", "install", "2026-07-30T00:00:00Z")

    dispatcher.dispatch((event,))

    assert received == [event]
    assert "after-commit subscriber failed" in caplog.text
