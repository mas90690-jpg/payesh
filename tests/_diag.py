"""پیدا کردن اسم attributeهای BaleCollector."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.bale import BaleCollector

c = BaleCollector(headless=True, max_chats=1)

print("=" * 55)
print("Attributeها:")
print("=" * 55)

for name in sorted(vars(c)):
    val = getattr(c, name)
    if callable(val):
        print(f"  {name}()")
    else:
        s = repr(val)
        if len(s) > 50:
            s = s[:47] + "..."
        print(f"  {name} = {s}")