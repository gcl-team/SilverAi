"""
SilverAi Demo
Run this script to see the library in action.
"""

from silver_ai.core import DRY_RUN_FLAG, GuardViolationError, guard
from silver_ai.rules import BatteryMin, MaxTemp, RequireConnectivity


# Mock a hardware driver
class HardwareDriver:
    def move_to(self, zone):
        print(f"ROBOT MOVING TO {zone}...")


driver = HardwareDriver()


class IndustrialRobot:
    def __init__(self, battery=100, temp=50, connection="WIFI"):
        self.state = {"battery": battery, "temperature": temp, "connection": connection}
        # Default Dry Run status
        setattr(self, DRY_RUN_FLAG, False)

    # Scenario 1: Strict safety (Standard usage)
    @guard(rules=[BatteryMin(20), RequireConnectivity("WIFI")])
    def clean_zone(self, zone: str):
        driver.move_to(zone)
        return "Cleaned"

    # Scenario 2: Critical Operation (Crash on failure)
    @guard(rules=[MaxTemp(80)], on_fail="raise")
    def emergency_shutdown(self):
        print("🚨 SHUTTING DOWN SYSTEM")
        return "Shutdown Complete"


def run_demo():
    print("--- 🛡️ SilverAi Demo ---\n")

    # 1. HAPPY PATH
    print("1. Testing Healthy Robot:")
    robot = IndustrialRobot(battery=80, connection="WIFI")
    result = robot.clean_zone("Zone A")
    print(f"Result: {result}\n")

    # 2. FAIL PATH (Battery Low)
    print("2. Testing Low Battery Robot (Zero-Crash):")
    dying_robot = IndustrialRobot(battery=10)
    result = dying_robot.clean_zone("Zone B")
    print(f"Result: {result}")
    print("NOTE: Script did not crash! Agent can read the error above.\n")

    # 3. DRY RUN PATH
    print("3. Testing Dry Run (Simulation):")
    setattr(robot, DRY_RUN_FLAG, True)
    result = robot.clean_zone("Zone C")
    print(f"Result: {result}")
    print("NOTE: Rules passed, but hardware was NOT touched.\n")

    # 4. EXCEPTION PATH
    print("4. Testing 'on_fail=raise' (Overheating):")
    hot_robot = IndustrialRobot(temp=95)
    try:
        hot_robot.emergency_shutdown()
    except GuardViolationError as e:
        print(f"CAUGHT EXCEPTION: {e}")
        print("NOTE: Script crashed as requested via on_fail='raise'.")


if __name__ == "__main__":
    run_demo()
