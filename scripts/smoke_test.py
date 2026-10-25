"""تست سریع سلامت پایه"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

def main():
    print("=== Smoke Test ===\n")
    tests = [
        ("core.brain.BrainMemory", "from core.brain import BrainMemory"),
        ("core.safe_brain.SafeBrain", "from core.safe_brain import SafeBrain"),
        ("core.adapters.AccessManager", "from core.adapters.access_manager import AccessManager"),
        ("core.kernel.EventBus", "from core.kernel.event_bus import EventBus"),
        ("core.database.create_database", "from core.database.factory import create_database"),
        ("core.kernel.logger", "from core.kernel.logger import get_logger"),
        ("collectors.telegram", "import collectors.telegram"),
        ("handlers.admin_handler", "import handlers.admin_handler"),
    ]
    
    failed = 0
    for name, stmt in tests:
        try:
            exec(stmt, {})
            print(f"OK   {name}")
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            failed += 1
    
    print(f"\nنتیجه: {len(tests)-failed}/{len(tests)} موفق")
    return 1 if failed else 0

if __name__ == '__main__':
    sys.exit(main())