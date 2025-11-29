from silver_ai.core import guard

#from silver_ai.rules import Rule

# 1. Define a dummy rule (since we haven't written real ones yet)
class AlwaysFalse():
    def check(self):
        return False
    def error_message(self):
        return "Computer says no."

# 2. Use the guard
@guard(rules=[AlwaysFalse()])
def launch_rocket():
    print("🚀 Rocket Launched!")

# 3. Run it
if __name__ == "__main__":
    print("Attempting launch...")
    result = launch_rocket()
    print(f"Result: {result}")