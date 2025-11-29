from silver_ai.core import guard

def test_guard_execution():
    """
    Verify that the decorated function actually runs.
    """
    # Arrange
    @guard()
    def risky_function():
        return "SUCCESS"

    # Act
    result = risky_function()

    # Assert
    assert result == "SUCCESS"