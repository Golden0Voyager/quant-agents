from cli.batch_dashboard import BatchDashboard


def test_dashboard_initial_state():
    bd = BatchDashboard(total=5, profile_name="test")
    assert bd.total == 5
    assert bd.completed == 0
    assert bd.failed == 0
    assert bd.current_ticker is None


def test_dashboard_update_progress():
    bd = BatchDashboard(total=5, profile_name="test")
    bd.update_progress(current_ticker="AAPL", completed=2, failed=1)
    assert bd.current_ticker == "AAPL"
    assert bd.completed == 2
    assert bd.failed == 1


def test_dashboard_agent_status():
    bd = BatchDashboard(total=3, profile_name="test")
    bd.update_agent_status("Market Analyst", "completed")
    bd.update_agent_status("Trader", "in_progress")
    assert bd.agent_status["Market Analyst"] == "completed"
    assert bd.agent_status["Trader"] == "in_progress"


def test_dashboard_reset_for_next_stock():
    bd = BatchDashboard(total=3, profile_name="test")
    bd.update_agent_status("Market Analyst", "completed")
    bd.reset_for_next_stock()
    assert bd.agent_status == {}
    assert bd.current_report is None


def test_reset_for_next_stock_preserves_messages():
    """reset_for_next_stock 应保留批次消息（batch 模式下不切消息）。"""
    bd = BatchDashboard(total=3, profile_name="test")
    bd.add_message("System", "批次初始化")
    bd.add_message("System", "数据预检")
    assert len(bd.messages) == 2

    bd.reset_for_next_stock()
    assert len(bd.messages) == 2, "reset_for_next_stock 不应清空消息"
    assert "数据预检" in str(bd.messages[1])
