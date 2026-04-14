from backend.orchestrator.routing.implicit_quality import ImplicitQualityTracker

def test_retry_detection_marks_previous_as_zero():
    tracker = ImplicitQualityTracker()
    tracker.on_task_complete(task_id="t1", task_hash="abc123", completed_at=1000.0)
    result = tracker.on_task_submitted(task_hash="abc123", submitted_at=1050.0)
    assert result is not None
    task_id, signal = result
    assert task_id == "t1"
    assert signal == 0.0

def test_accept_detection_marks_previous_as_positive():
    tracker = ImplicitQualityTracker()
    tracker.on_task_complete(task_id="t1", task_hash="abc123", completed_at=1000.0)
    result = tracker.on_task_submitted(task_hash="xyz999", submitted_at=1060.0)
    assert result is not None
    task_id, signal = result
    assert task_id == "t1"
    assert signal == 0.6

def test_no_signal_after_10_minutes():
    tracker = ImplicitQualityTracker()
    tracker.on_task_complete(task_id="t1", task_hash="abc123", completed_at=1000.0)
    result = tracker.on_task_submitted(task_hash="xyz999", submitted_at=1660.0)
    assert result is None

def test_retry_window_5_minutes():
    tracker = ImplicitQualityTracker()
    tracker.on_task_complete(task_id="t1", task_hash="abc123", completed_at=1000.0)
    # Same hash, 6 minutes later — outside retry window but inside accept window, different hash check
    result = tracker.on_task_submitted(task_hash="abc123", submitted_at=1361.0)
    # Same hash but outside 5-min retry window → should NOT be a retry
    # But it's within 10-min accept window... but same hash ≠ "different task"
    # So it returns None (no accept for same hash)
    assert result is None

def test_no_signal_when_no_pending():
    tracker = ImplicitQualityTracker()
    result = tracker.on_task_submitted(task_hash="anything", submitted_at=1000.0)
    assert result is None
